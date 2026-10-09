"""The whole path inside the real cycle: the agent's choice -> the payer -> the contract -> Circle.

A scripted brain stands in for Claude; everything after it is the code that runs in the service.
"""

from datetime import timedelta, timezone

from agent.agent import SessionRun
from agent.models import Autonomy, Choice, Usage
from agent.reflexes.cycle import run_cycle
from agent.think import BrainSetup
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


def test_a_payment_the_agent_schedules_waits_for_its_date(journal):  # noqa: F811
    payer, _, circle = make_payer(matching_ledger())
    tomorrow = (NOW.astimezone(LAGOS) + timedelta(days=1)).date().isoformat()
    brain = OneChoiceBrain("schedule_payment", pay_on=tomorrow)

    first = cycle(journal, payer, brain, NOW)
    assert circle.sent == []
    assert any("scheduled it for" in d.reason for d in first.plan.deferred)

    cycle(journal, payer, brain, NOW + timedelta(days=1))
    assert len(circle.sent) == 1  # paid on its date, as the agent decided


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


def test_the_agent_is_not_woken_again_when_nothing_happened(journal):  # noqa: F811
    payer, _, _ = make_payer(matching_ledger())
    brain = OneChoiceBrain("hold")
    cycle(journal, payer, brain, NOW)
    cycle(journal, payer, brain, NOW + timedelta(minutes=15))

    assert brain.sessions == 1
