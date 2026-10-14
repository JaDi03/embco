"""What the agent did itself never wakes it: its own payments are not a change of money, an
invoice it paid does not wake it when it changes, and an alarm about that invoice does not ring.
Nothing wakes it only because time passed."""

import secrets
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agent.agent import SessionRun
from agent.guardrails.controls.base import Finding, Outcome
from agent.guardrails.rules import Action, Decision, PolicyConfig, fingerprint
from agent.memory import Change, ChangeKind, SqliteJournal
from agent.models import AgentDecision, Alarm, Choice, Session, Wake
from agent.reflexes.cycle import run_cycle
from agent.reflexes.funds import money_wake
from agent.reflexes.wake import pending_alarms, wakes_for
from agent.think import BrainSetup, think
from agent.tools import Toolbox, ToolError
from services.payments import PaymentEvent, PaymentStatus
from services.shops import activity
from support import WALLET_A, FakeLedger

NOW = datetime(2026, 10, 13, 19, 49, tzinfo=UTC)
LAGOS = timezone(timedelta(hours=1))
POLICY = PolicyConfig(max_per_payment=Decimal(250), weekly_budget=Decimal(2000))
PAID, OPEN = "ACC-PINV-2026-00012", "ACC-PINV-2026-00017"


def decision(invoice, amount="184"):
    return Decision(invoice=invoice, supplier="Northern Grains", amount=Decimal(amount),
                    due_date=date(2026, 10, 19), action=Action.PAY, reasons=("all passed",),
                    findings=(Finding("three_way_match", Outcome.PASS, "ok"),))


def payment(invoice=PAID, status=PaymentStatus.COMPLETE):
    return PaymentEvent(invoice=invoice, status=status, at=NOW, supplier="Northern Grains",
                        payee=WALLET_A, amount=Decimal("184.0"),
                        invoice_ref="0x" + secrets.token_hex(32))


def wakes(changes=(), sessions=(), done=frozenset({PAID}), decisions=None, now=NOW):
    decisions = decisions or [decision(PAID), decision(OPEN, "476")]
    standing = {}
    return wakes_for(now=now, zone=LAGOS, decisions=decisions, changes=changes,
                     sessions=list(sessions), standing=standing, done=set(done), payments=[],
                     round_hour=25)  # no round in these tests


# ---- its own payment is not a change of money


class PaysOnce:
    """Pays during its session: the money it can see drops while it runs."""

    model = "scripted"

    def __init__(self, money):
        self.money = money

    def run(self, toolbox, briefing):
        self.money["available_to_pay"] = "165.945618"
        toolbox.payments.append(payment())
        for invoice in toolbox.undecided():
            toolbox.call("hold", {"invoice": invoice, "reason": "waiting"})
        toolbox.call("finish", {"summary": "Paid one."})
        return SessionRun(finished=True, steps=1, usage=None)


def test_after_paying_the_session_keeps_the_money_it_left_and_is_not_woken_by_it(tmp_path):
    money = {"owner_balance": "349.945618", "available_to_pay": "349.945618",
             "payments_authorized": "yes"}
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        setup = BrainSetup(brain=PaysOnce(money), zone=LAGOS, funds=lambda: dict(money))
        think(setup, journal=journal, ledger=None, decisions=[decision(OPEN, "476")],
              changes=(), policy=POLICY, done=set(), now=NOW)
        sessions = journal.sessions()
    assert sessions[-1].money["available_to_pay"] == "165.945618"
    assert money_wake(sessions, dict(money), [decision(OPEN, "476")], set()) is None
    added = {**money, "available_to_pay": "665.945618"}  # the owner adds 500: worth a look
    assert money_wake(sessions, added, [decision(OPEN, "476")], set()).kind == "money"


# ---- an invoice it paid does not wake it


def test_a_paid_invoice_that_changes_does_not_wake_the_agent():
    paid = Change(decision(PAID), ChangeKind.CHANGED, None, "the reasons changed")
    other = Change(decision(OPEN, "476"), ChangeKind.CHANGED, None, "the wallet was proven")
    found = wakes(changes=[paid, other])
    assert [(w.kind, w.invoice) for w in found if w.kind == "changed"] == [("changed", OPEN)]


def test_a_paid_invoice_that_changes_leaves_no_line_in_the_activity(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        report = run_cycle(FakeLedger(), journal, POLICY, "TEST Shop", at=NOW)
    invoices = [c.decision.invoice for c in report.changes]
    assert invoices, "the fake ERP has open invoices"
    shown = {e.get("invoice") for e in activity.cycle_events(report, 15)}
    assert set(invoices) <= shown
    paid = replace(report, already_paid=tuple(invoices))
    assert not {e.get("invoice") for e in activity.cycle_events(paid, 15)} & set(invoices)


# ---- an alarm about an invoice it paid does not ring


def session(at, alarms=()):
    return Session(at=at, wakes=(Wake("round", "r"),), finished=True, alarms=tuple(alarms))


def test_an_alarm_about_a_paid_invoice_does_not_ring_and_others_do():
    ring = NOW - timedelta(minutes=1)
    alarms = [Alarm(ring, "check the flour", set_at=NOW - timedelta(days=1), invoice=PAID),
              Alarm(ring, "look at Adeyemi", set_at=NOW - timedelta(days=1), invoice=OPEN),
              Alarm(ring, "the weekly room renews", set_at=NOW - timedelta(days=1))]
    found = wakes(sessions=[session(NOW - timedelta(days=1), alarms)])
    assert sorted(w.text for w in found if w.kind == "alarm") == [
        "Your alarm: look at Adeyemi", "Your alarm: the weekly room renews"]
    later = [replace(a, at=NOW + timedelta(days=4)) for a in alarms]
    assert [a.why for a in pending_alarms([session(NOW, later)], NOW, {PAID})] == [
        "look at Adeyemi", "the weekly room renews"]


def box(journal, decisions):
    return Toolbox(decisions=decisions, ledger=None, journal=journal, policy=POLICY, now=NOW,
                   zone=LAGOS)


def test_an_alarm_names_an_open_invoice_or_none(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        b = box(journal, [decision(OPEN, "476")])
        b.call("set_alarm", {"at": "2026-10-21T09:00", "why": "look again", "invoice": OPEN})
        b.call("set_alarm", {"at": "2026-10-21T09:00", "why": "the week renews", "invoice": ""})
        with pytest.raises(ToolError, match="not an open invoice"):
            b.call("set_alarm", {"at": "2026-10-21T09:00", "why": "x", "invoice": "PINV-9"})
        assert [a.invoice for a in b.alarms] == [OPEN, None]


def test_the_invoice_of_an_alarm_is_kept_across_a_restart(tmp_path):
    alarm = Alarm(NOW + timedelta(days=4), "look again", set_at=NOW, invoice=OPEN)
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        journal.record_session(session(NOW, [alarm]))
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        [kept] = journal.sessions()[-1].alarms
    assert kept.invoice == OPEN


# ---- time alone does not wake it


def test_hours_without_news_do_not_wake_the_agent():
    held = decision(OPEN, "476")
    standing = {OPEN: AgentDecision(OPEN, fingerprint(held), Choice.HOLD, "waiting")}
    assert wakes_for(now=NOW, zone=LAGOS, decisions=[held], changes=(),
                     sessions=[session(NOW - timedelta(hours=10))], standing=standing,
                     done=set(), payments=[], round_hour=25) == []
