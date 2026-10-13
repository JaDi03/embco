"""The whole path inside the real cycle: the agent's choice -> the payer -> the contract -> Circle.

A scripted brain stands in for Claude; everything after it is the code that runs in the service.
"""

from datetime import timedelta, timezone

from agent.agent import SessionRun
from agent.models import Autonomy, Choice, Usage
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


def cycle(journal, payer, brain, at, autonomy=Autonomy.ACT):  # noqa: F811
    setup = BrainSetup(brain=brain, zone=LAGOS, autonomy=autonomy)
    return run_cycle(payer.ledger, journal, POLICY, "TEST Shop", at=at, payments=payer,
                     brain=setup)


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


def test_the_agent_is_not_woken_again_when_nothing_happened(journal):  # noqa: F811
    payer, _, _ = make_payer(matching_ledger())
    brain = OneChoiceBrain("hold")
    cycle(journal, payer, brain, NOW)
    cycle(journal, payer, brain, NOW + timedelta(minutes=15))

    assert brain.sessions == 1
