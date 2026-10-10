"""When the money will not reach a coming payment, the agent is woken with the figures and tells
the owner on its own, without waiting to be asked."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agent.agent import SessionRun
from agent.guardrails.rules import Action, Decision, PolicyConfig, fingerprint
from agent.memory import SqliteJournal
from agent.models import AgentDecision, Choice, Usage
from agent.reflexes.funds import funds_wake
from agent.think import BrainSetup, think
from agent.tools import Toolbox, ToolError

TODAY = date(2026, 10, 10)
NOW = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
LAGOS = timezone(timedelta(hours=1))
SHORT = {"available_to_pay": "383.444158", "owner_balance": "383.444158",
         "payments_authorized": "yes"}


def invoice(name, amount, due=None, action=Action.PAY):
    return Decision(invoice=name, supplier="Central Provisions Ltd", amount=Decimal(amount),
                    due_date=due, action=action, reasons=("r",), findings=())


PINV_16 = invoice("PINV-00016", "420", date(2026, 10, 9))


def scheduled(d, day):
    return {d.invoice: AgentDecision(invoice=d.invoice, fingerprint=fingerprint(d),
                                     choice=Choice.SCHEDULE, reason="room", pay_on=day)}


def test_a_scheduled_payment_bigger_than_the_money_wakes_the_agent_with_the_figures():
    wake = funds_wake([PINV_16], scheduled(PINV_16, date(2026, 10, 14)), set(), SHORT, TODAY,
                      set())
    assert wake.kind == "funds"
    assert "by 2026-10-14" in wake.text and "420 USDC" in wake.text
    assert "383.444158 USDC can be paid" in wake.text
    assert funds_wake([PINV_16], scheduled(PINV_16, date(2026, 10, 14)), set(), SHORT, TODAY,
                      {wake.key}) is None  # once per day and figure


def test_enough_money_or_nothing_expected_wakes_nobody():
    plenty = {**SHORT, "available_to_pay": "1000"}
    assert funds_wake([PINV_16], scheduled(PINV_16, date(2026, 10, 14)), set(), plenty, TODAY,
                      set()) is None
    held = {PINV_16.invoice: AgentDecision(invoice=PINV_16.invoice,
                                           fingerprint=fingerprint(PINV_16), choice=Choice.HOLD,
                                           reason="x")}
    assert funds_wake([PINV_16], held, set(), SHORT, TODAY, set()) is None
    far = invoice("PINV-9", "900", date(2026, 11, 30))
    assert funds_wake([far], {}, set(), SHORT, TODAY, set()) is None  # beyond 7 days
    assert funds_wake([PINV_16], {}, set(), None, TODAY, set()) is None  # unknown: no guess


def test_payments_add_up_until_the_day_the_money_runs_out():
    a = invoice("PINV-A", "200", date(2026, 10, 11))
    b = invoice("PINV-B", "200", date(2026, 10, 12))
    wake = funds_wake([a, b], {}, set(), SHORT, TODAY, set())
    assert "by 2026-10-12" in wake.text and "400 USDC" in wake.text


class Warner:
    model = "scripted"

    def run(self, toolbox, briefing):
        if "Money will not reach" in briefing:
            toolbox.call("notify_owner", {"text": "On 14 Oct PINV-00016 needs 420 USDC but only "
                                                  "383.44 can be paid. Add at least 36.56 USDC."})
        for invoice_name in toolbox.undecided():
            toolbox.call("hold", {"invoice": invoice_name, "reason": "waiting"})
        toolbox.call("finish", {"summary": "Warned the owner."})
        return SessionRun(finished=True, steps=1, usage=Usage())


def test_the_agent_tells_the_owner_on_its_own(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        decided = scheduled(PINV_16, date(2026, 10, 14))[PINV_16.invoice]
        from agent.models import Session, Wake
        journal.record_session(Session(at=NOW - timedelta(hours=1), wakes=(Wake("start", "s"),),
                                       finished=True, decisions=(decided,)))
        setup = BrainSetup(brain=Warner(), zone=LAGOS, funds=lambda: SHORT)
        thought = think(setup, journal=journal, ledger=None, decisions=[PINV_16], changes=(),
                        policy=PolicyConfig(max_per_payment=Decimal(420),
                                            weekly_budget=Decimal(2000)),
                        done=set(), now=NOW)
    assert thought.session.replies == ("On 14 Oct PINV-00016 needs 420 USDC but only 383.44 can "
                                       "be paid. Add at least 36.56 USDC.",)


def test_notices_are_few_and_never_point_around_a_limit(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        box = Toolbox(decisions=[PINV_16], ledger=None, journal=journal,
                      policy=PolicyConfig(max_per_payment=Decimal(420),
                                          weekly_budget=Decimal(2000)),
                      now=NOW, zone=LAGOS)
        with pytest.raises(ToolError, match="rewrite"):
            box.call("notify_owner", {"text": "Split it in two payments."})
        for _ in range(3):
            box.call("notify_owner", {"text": "Add USDC to your wallet."})
        with pytest.raises(ToolError, match="at most 3"):
            box.call("notify_owner", {"text": "Add USDC to your wallet."})
