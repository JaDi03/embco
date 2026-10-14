"""The whole path inside the real cycle: the agent's choice -> the payer -> the contract -> Circle.

A scripted brain stands in for Claude; everything after it is the code that runs in the service.
"""

import json
from datetime import timedelta, timezone

from agent.agent import SessionRun
from agent.models import Autonomy, Choice, Usage, Wake
from agent.reflexes.cycle import run_cycle
from agent.think import BrainSetup
from agent.tools import ToolError
from services.payments import PaymentStatus
from test_payments import NOW, POLICY, journal, make_payer, matching_ledger  # noqa: F401

LAGOS = timezone(timedelta(hours=1))


class OneChoiceBrain:
    """Decides every undecided invoice the same way, then finishes; or fails."""

    model = "scripted"

    def __init__(self, tool: str, fail: bool = False, **extra: str) -> None:
        self.tool, self.fail, self.extra, self.sessions = tool, fail, extra, 0

    def run(self, toolbox, briefing):
        self.sessions += 1
        if self.fail:
            return SessionRun(finished=False, steps=0, usage=Usage(), error="model down")
        for invoice in toolbox.undecided():
            toolbox.call(self.tool, {"invoice": invoice, "reason": "scripted", **self.extra})
        toolbox.call("finish", {"summary": "Done."})
        return SessionRun(finished=True, steps=1, usage=Usage())


def cycle(journal, payer, brain, at, autonomy=Autonomy.ACT, wakes=()):  # noqa: F811
    setup = BrainSetup(brain=brain, zone=LAGOS, autonomy=autonomy)
    return run_cycle(payer.ledger, journal, POLICY, "TEST Shop", at=at, payments=payer,
                     brain=setup, wakes=list(wakes))


AGAIN = (Wake("limits", "The owner changed the limits in the shop contract."),)  # wakes it again


def test_what_the_agent_chooses_to_pay_now_is_paid_through_the_contract(journal):  # noqa: F811
    payer, chain, circle = make_payer(matching_ledger())
    report = cycle(journal, payer, OneChoiceBrain("pay_now"), NOW)

    assert report.thought.session.finished
    assert report.thought.agent["PINV-1"].choice is Choice.PAY_NOW
    assert len(chain.simulated) == 1 and len(circle.sent) == 1
    assert report.settlement.events[-1].status is PaymentStatus.COMPLETE


class SchedulesThenPays:
    """Schedules every invoice for tomorrow; when woken on that day, pays it."""

    model = "scripted"

    def __init__(self, pay_on: str) -> None:
        self.pay_on, self.briefings, self.results = pay_on, [], []

    def run(self, toolbox, briefing):
        self.briefings.append(briefing)
        for invoice in toolbox.undecided():
            toolbox.call("schedule_payment", {"invoice": invoice, "pay_on": self.pay_on,
                                              "reason": "pay it the day before it is due"})
        if "You planned to pay" in briefing:
            self.results.append(toolbox.call("pay_now", {"invoice": "PINV-1",
                                                         "reason": "the day I planned"}))
        toolbox.call("finish", {"summary": "Done."})
        return SessionRun(finished=True, steps=1, usage=Usage())


def test_on_the_day_it_scheduled_the_agent_is_woken_and_pays_itself(journal):  # noqa: F811
    payer, _, circle = make_payer(matching_ledger())
    tomorrow = (NOW.astimezone(LAGOS) + timedelta(days=1)).date().isoformat()
    brain = SchedulesThenPays(tomorrow)

    first = cycle(journal, payer, brain, NOW)
    assert circle.sent == []
    assert any("scheduled it for" in d.reason for d in first.plan.deferred)

    cycle(journal, payer, brain, NOW + timedelta(days=1))
    assert "You planned to pay PINV-1" in brain.briefings[-1]
    assert len(circle.sent) == 1  # the agent paid it, nobody paid after it
    assert '"status": "paid on Arc' in brain.results[-1]


def test_the_agent_sees_what_its_payment_did(journal):  # noqa: F811
    payer, _, circle = make_payer(matching_ledger())
    seen = []

    class Pays:
        model = "scripted"

        def run(self, toolbox, briefing):
            seen.append(toolbox.call("pay_now", {"invoice": "PINV-1", "reason": "due soon"}))
            seen.append(toolbox.call("payment_status", {"invoice": "PINV-1"}))
            toolbox.call("finish", {"summary": "Paid PINV-1."})
            return SessionRun(finished=True, steps=3, usage=Usage())

    report = cycle(journal, payer, Pays(), NOW)
    assert len(circle.sent) == 1
    assert '"transaction": "0x' in seen[0] and '"status": "paid on Arc' in seen[1]
    assert [e.status for e in report.settlement.events] == [PaymentStatus.SUBMITTED,
                                                            PaymentStatus.COMPLETE]


def test_circles_own_error_reaches_the_agent_and_nothing_is_sent_twice(journal):  # noqa: F811
    from services.circle import CircleError
    from test_payments import FakeCircle

    class NoFees(FakeCircle):
        def execute_contract(self, *args, **kwargs):
            raise CircleError("Circle returned HTTP 400 for /v1/w3s/developer/transactions/"
                              "contractExecution (code 155258: the asset amount owned by the "
                              "wallet is insufficient for the transaction.)")

    payer, _, _ = make_payer(matching_ledger(), circle=NoFees())
    errors = []

    class Tries:
        model = "scripted"

        def run(self, toolbox, briefing):
            try:
                toolbox.call("pay_now", {"invoice": "PINV-1", "reason": "due soon"})
            except ToolError as error:
                errors.append(str(error))
            toolbox.call("hold", {"invoice": "PINV-1", "reason": "waiting for fee money"})
            toolbox.call("notify_owner", {"text": "My wallet has no USDC for network fees."})
            toolbox.call("finish", {"summary": "Could not pay: no fee money."})
            return SessionRun(finished=True, steps=4, usage=Usage())

    report = cycle(journal, payer, Tries(), NOW)
    assert "insufficient for the transaction" in errors[0]
    assert report.thought.session.replies == ("My wallet has no USDC for network fees.",)
    assert "insufficient" in report.settlement.events[-1].reason


def test_observe_mode_sends_nothing(journal):  # noqa: F811
    payer, chain, circle = make_payer(matching_ledger())
    cycle(journal, payer, OneChoiceBrain("pay_now"), NOW, autonomy=Autonomy.OBSERVE)

    assert chain.simulated == [] and circle.sent == []


def test_when_the_model_fails_nothing_new_is_paid(journal):  # noqa: F811
    payer, chain, circle = make_payer(matching_ledger())
    report = cycle(journal, payer, OneChoiceBrain("pay_now", fail=True), NOW)

    assert report.thought.session.finished is False
    assert circle.sent == []
    assert any("waiting for the agent" in d.reason for d in report.plan.deferred)


def test_a_refusal_the_agent_already_saw_does_not_wake_it_again(journal):  # noqa: F811
    payer, chain, _ = make_payer(matching_ledger())
    payer.clock = lambda: NOW + timedelta(minutes=1)  # the refusal comes during the session
    chain.revert = "PayeeNotApproved"

    class TriesThenHolds:
        model, sessions = "scripted", 0

        def run(self, toolbox, briefing):
            self.sessions += 1
            try:
                toolbox.call("pay_now", {"invoice": "PINV-1", "reason": "due soon"})
            except ToolError:
                toolbox.call("hold", {"invoice": "PINV-1", "reason": "the wallet is not approved"})
            toolbox.call("finish", {"summary": "Held PINV-1."})
            return SessionRun(finished=True, steps=2, usage=Usage())

    brain = TriesThenHolds()
    cycle(journal, payer, brain, NOW)
    cycle(journal, payer, brain, NOW + timedelta(minutes=15))
    assert brain.sessions == 1


class PaysWhenTold:
    """Chooses pay_now for what is undecided, and again when told a choice did not go out."""

    model, sessions = "scripted", 0

    def run(self, toolbox, briefing):
        self.sessions += 1
        todo = toolbox.undecided() or (["PINV-1"] if "has not gone out" in briefing else [])
        for invoice in todo:
            toolbox.call("pay_now", {"invoice": invoice, "reason": "due soon"})
        toolbox.call("finish", {"summary": "Done."})
        return SessionRun(finished=True, steps=1, usage=Usage())


def test_a_choice_to_pay_that_never_went_out_wakes_the_agent_once_it_can_pay(journal):  # noqa: F811
    payer, _, circle = make_payer(matching_ledger())
    brain = PaysWhenTold()
    cycle(journal, payer, brain, NOW, autonomy=Autonomy.OBSERVE)  # paused: recorded, not sent
    cycle(journal, payer, brain, NOW + timedelta(minutes=15), autonomy=Autonomy.OBSERVE)
    assert brain.sessions == 1 and circle.sent == []  # no loop while payments are paused

    cycle(journal, payer, brain, NOW + timedelta(minutes=30))  # the owner resumed payments
    assert brain.sessions == 2 and len(circle.sent) == 1
    cycle(journal, payer, brain, NOW + timedelta(minutes=45))
    assert brain.sessions == 2  # paid: nothing more to wake it for


class Spends:
    """Decides on hold and reports what the session cost in tokens, as Claude Haiku 5.5."""

    def __init__(self, usage, model="claude-haiku-5-5"):
        self.usage, self.model, self.sessions = usage, model, 0

    def run(self, toolbox, briefing):
        self.sessions += 1
        for invoice in toolbox.undecided():
            toolbox.call("hold", {"invoice": invoice, "reason": "waiting"})
        toolbox.call("finish", {"summary": "Done."})
        return SessionRun(finished=True, steps=1, usage=self.usage)


def woken_twice(journal, brain):  # noqa: F811
    payer, _, _ = make_payer(matching_ledger())
    first = cycle(journal, payer, brain, NOW)
    second = cycle(journal, payer, brain, NOW + timedelta(hours=1), wakes=AGAIN)
    return first, second


def test_the_daily_budget_is_in_dollars_and_cache_reads_do_not_eat_it(journal):  # noqa: F811
    cheap = Spends(Usage(cache_read_tokens=3_000_000))  # 3M cached tokens: $0.03
    _, second = woken_twice(journal, cheap)
    assert cheap.sessions == 2 and second.thought.skipped == ""


def test_past_one_dollar_a_day_the_agent_is_not_woken(journal):  # noqa: F811
    costly = Spends(Usage(output_tokens=2_100_000))  # $1.05 of output
    _, second = woken_twice(journal, costly)
    assert costly.sessions == 1
    assert second.thought.skipped.startswith("the agent's daily budget is used up ($1.05 of $1")


def test_a_model_without_a_price_counts_by_tokens(journal):  # noqa: F811
    unknown = Spends(Usage(input_tokens=2_000_000), model="some-other-model")
    _, second = woken_twice(journal, unknown)
    assert unknown.sessions == 1 and "budget is used up" in second.thought.skipped


def test_the_owner_is_told_once_why_the_agent_was_not_woken(tmp_path):
    from agent.think import Thought
    from services.shops import chat
    from services.shops.agent import not_woken

    out = Thought(agent={}, skipped="the agent's daily budget is used up ($1.05 of $1)")
    first = not_woken(tmp_path, out, owner_waiting=False, at=NOW)
    assert "not woken" in first[0]["text"] and "nothing new is paid" in first[0]["text"]
    chat.append_chat(tmp_path, first)
    assert not_woken(tmp_path, out, owner_waiting=True, at=NOW) == []  # said already
    failed = Thought(agent={}, skipped="the last session failed; trying again soon")
    assert not_woken(tmp_path, failed, owner_waiting=False, at=NOW) == []  # only if waiting
    assert not_woken(tmp_path, failed, owner_waiting=True, at=NOW)


class Reads:
    """Keeps every briefing, holds what is undecided and finishes with a summary."""

    model = "scripted"

    def __init__(self):
        self.briefings = []

    def run(self, toolbox, briefing):
        self.briefings.append(json.loads(briefing.split("\n\n", 1)[1]))
        for invoice in toolbox.undecided():
            toolbox.call("hold", {"invoice": invoice, "reason": "asking about the receipt"})
        toolbox.call("finish", {"summary": f"Summary {len(self.briefings)}."})
        return SessionRun(finished=True, steps=1, usage=Usage())


def test_the_agent_sees_what_stops_an_invoice_apart_from_what_to_weigh(journal):  # noqa: F811
    from support import make_invoice

    ledger = matching_ledger()
    bare = make_invoice(currency="USD").lines[0].model_copy(update={"purchase_receipt": None})
    ledger.pending = [make_invoice(currency="USD", lines=(bare,))]
    payer, _, _ = make_payer(ledger)
    brain = Reads()
    cycle(journal, payer, brain, NOW)
    [row] = brain.briefings[0]["open_invoices"]
    assert row["checks_say"] == "PAY" and row["stops"] == []
    assert row["to_weigh"] == ["COLA: not linked to an order and a receipt"]


def test_the_agent_is_told_what_it_already_said_to_the_owner(journal):  # noqa: F811
    payer, _, _ = make_payer(matching_ledger())
    brain = Reads()
    cycle(journal, payer, brain, NOW)
    cycle(journal, payer, brain, NOW + timedelta(hours=1), wakes=AGAIN)
    assert brain.briefings[0]["already_told_the_owner"] == []
    [told] = brain.briefings[1]["already_told_the_owner"]
    assert told["summary"] == "Summary 1."


def test_the_agent_is_not_woken_again_when_nothing_happened(journal):  # noqa: F811
    payer, _, _ = make_payer(matching_ledger())
    brain = OneChoiceBrain("hold")
    cycle(journal, payer, brain, NOW)
    cycle(journal, payer, brain, NOW + timedelta(minutes=15))

    assert brain.sessions == 1
