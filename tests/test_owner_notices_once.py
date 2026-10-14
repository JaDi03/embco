"""What reaches the owner is said once: a payment is one line in the chat and in the activity,
however many steps it went through; a reminder keeps the agent's question; and a hold that only
lost reasons does not wake the agent. Built on the pilot's 14-Oct checks (Ogun Dairy 00015/00016).
"""

import secrets
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from agent.agent import SessionRun
from agent.guardrails.controls.base import Finding, Outcome
from agent.guardrails.rules import Action, Decision, PolicyConfig, fingerprint
from agent.memory import Change, ChangeKind, SqliteJournal
from agent.memory.changes import compare
from agent.models import AgentDecision, Choice, Session, Wake
from agent.reflexes.cycle import run_cycle
from agent.think import BrainSetup, Thought, think
from services.payments import PaymentEvent, PaymentStatus
from services.shops import activity
from services.shops.agent import paid_notices
from services.shops.questions import new_questions
from support import WALLET_A, FakeLedger

NOW = datetime(2026, 10, 14, 1, 21, tzinfo=UTC)
LAGOS = timezone(timedelta(hours=1))
POLICY = PolicyConfig(max_per_payment=Decimal(250), weekly_budget=Decimal(2000))
OGUN, PAID, TWIN = "Ogun Dairy & Provisions", "ACC-PINV-2026-00015", "ACC-PINV-2026-00016"
SAME_BILL = f"same supplier invoice number as {PAID}"
FIRST = "first payment to this supplier, wallet 0x5487...6e80"


def payment(status, invoice=PAID):
    return PaymentEvent(invoice=invoice, status=status, at=NOW, supplier=OGUN, payee=WALLET_A,
                        amount=Decimal("128.0"), invoice_ref="0x" + secrets.token_hex(32),
                        tx_hash="0x" + "ab" * 32, erp_entry="ACC-PAY-2026-00013")


STEPS = [payment(PaymentStatus.SUBMITTED), payment(PaymentStatus.COMPLETE),
         payment(PaymentStatus.RECORDED)]


# ---- a payment is one line


def test_a_payment_on_its_own_is_one_chat_line_not_one_per_step():
    out = Thought(agent={}, payments=tuple(STEPS))
    assert [e["text"] for e in paid_notices(out, owner_waiting=False)] == [
        "Paid 128 USDC to Ogun Dairy & Provisions (ACC-PINV-2026-00015)."]


def test_a_payment_that_failed_after_it_was_sent_is_not_told_as_paid():
    out = Thought(agent={}, payments=(STEPS[0], payment(PaymentStatus.FAILED)))
    assert paid_notices(out, owner_waiting=False) == []


def test_a_payment_is_one_line_in_the_activity_with_its_transaction():
    lines = activity.payment_events(NOW, STEPS)
    assert [e["text"] for e in lines] == [
        "Paid 128 USDC to Ogun Dairy & Provisions for ACC-PINV-2026-00015, recorded in ERPNext "
        "as ACC-PAY-2026-00013."]
    assert lines[0]["tx"] == STEPS[-1].tx_hash


def test_a_payment_still_on_its_way_shows_how_far_it_got():
    lines = activity.payment_events(NOW, STEPS[:1])
    assert [e["kind"] for e in lines] == ["sending"]


# ---- the activity does not repeat the agent


def agent_choice(invoice, choice=Choice.HOLD, decision=None):
    return AgentDecision(invoice=invoice, fingerprint=fingerprint(decision) if decision else "x",
                         choice=choice, reason="Same Ogun bill entered twice.")


def test_the_agent_answer_is_in_the_chat_not_again_in_the_activity():
    session = Session(at=NOW, wakes=(Wake("message", "The owner wrote to you."),),
                      finished=True, replies=("Done. I paid ACC-PINV-2026-00015.",),
                      summary="Paid Ogun Dairy ACC-PINV-2026-00015 (128 USDC).")
    texts = [e["text"] for e in activity.session_events(session)]
    assert not any(t.startswith("To you") for t in texts)
    assert "Paid Ogun Dairy ACC-PINV-2026-00015 (128 USDC)." in texts


def test_a_changed_invoice_the_agent_decided_on_has_only_the_agent_line(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        first = run_cycle(FakeLedger(), journal, POLICY, "TEST Shop", at=NOW)
    decided, other = TWIN, "ACC-PINV-2026-00017"
    changed = [Change(twin(SAME_BILL), ChangeKind.CHANGED, None, "a reason went"),
               Change(replace(twin(SAME_BILL), invoice=other), ChangeKind.CHANGED, None, "c")]
    session = Session(at=NOW, wakes=(Wake("changed", "c"),), finished=True,
                      decisions=(agent_choice(decided),))
    report = replace(first, changes=tuple(changed),
                     thought=Thought(agent={}, session=session))
    shown = {e.get("invoice") for e in activity.cycle_events(report, 15) if e["kind"] == "changed"}
    assert decided not in shown
    assert other in shown


# ---- the reminder keeps the agent's question


QUESTION = ("Ogun Dairy's new payout wallet 0x5487...6e80 was set in the ERP on 13 Oct at 03:38. "
            "Did Ogun ask you to change its wallet?")


def test_a_reminder_names_the_supplier_once_and_keeps_the_question(tmp_path):
    d = Decision(invoice=PAID, supplier=OGUN, amount=Decimal(128), due_date=date(2026, 10, 23),
                 action=Action.ASK, reasons=(FIRST,),
                 findings=(Finding("payee_wallet", Outcome.ASK, FIRST),))
    choice = replace(agent_choice(PAID, Choice.ASK, d), question=QUESTION)
    [entry] = new_questions(tmp_path, [d], {PAID: choice}, NOW, policy=POLICY,
                            funds=lambda: {"owner_balance": "165.94",
                                           "available_to_pay": "165.94"})
    assert entry["text"] == (
        "Ogun Dairy & Provisions, 128 USDC (ACC-PINV-2026-00015): needs you in the dashboard: "
        f"approve its wallet.\n{QUESTION}")
    assert entry["buttons"] is False


# ---- a hold that only lost reasons does not wake the agent


def twin(*reasons):
    return Decision(invoice=TWIN, supplier=OGUN, amount=Decimal(128), due_date=date(2026, 10, 23),
                    action=Action.HOLD, reasons=reasons,
                    findings=(Finding("duplicate", Outcome.HOLD, SAME_BILL),))


class Holds:
    """Holds every invoice it has to decide, and counts its sessions."""

    model = "scripted"

    def __init__(self):
        self.sessions = 0

    def run(self, toolbox, briefing):
        self.sessions += 1
        for invoice in toolbox.undecided():
            toolbox.call("hold", {"invoice": invoice, "reason": "Same Ogun bill entered twice."})
        toolbox.call("finish", {"summary": "Held the twin."})
        return SessionRun(finished=True, steps=1, usage=None)


def check(journal, brain, decision, at):
    """One check: compare with the last decision, record it, and let the reflexes decide."""
    change = compare(decision, journal.last_entry(decision.invoice))
    journal.record_run([change], [], POLICY, at)
    changes = [change] if change.kind is not ChangeKind.SAME else []
    setup = BrainSetup(brain=brain, zone=LAGOS, funds=lambda: None)
    return think(setup, journal=journal, ledger=None, decisions=[decision], changes=changes,
                 policy=POLICY, done=set(), now=at)


def test_a_hold_that_only_lost_a_reason_does_not_wake_the_agent(tmp_path):
    brain = Holds()
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        check(journal, brain, twin(SAME_BILL, FIRST), NOW)
        assert brain.sessions == 1
        # 01:36: paying 00015 dropped "first payment"; the twin is held for the same bill
        quiet = check(journal, brain, twin(SAME_BILL), NOW + timedelta(minutes=15))
        assert quiet.session is None and brain.sessions == 1
        later = check(journal, brain, twin(SAME_BILL), NOW + timedelta(minutes=30))
        assert later.session is None and brain.sessions == 1  # not "undecided" either


def test_a_hold_with_a_new_reason_still_wakes_the_agent(tmp_path):
    brain = Holds()
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        check(journal, brain, twin(SAME_BILL), NOW)
        woke = check(journal, brain, twin(SAME_BILL, FIRST), NOW + timedelta(minutes=15))
    assert brain.sessions == 2
    assert [w.kind for w in woke.session.wakes] == ["changed"]


def test_a_hold_that_turns_payable_still_wakes_the_agent(tmp_path):
    brain = Holds()
    payable = replace(twin(), action=Action.PAY, reasons=("all passed",))
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        check(journal, brain, twin(SAME_BILL), NOW)
        check(journal, brain, payable, NOW + timedelta(minutes=15))
    assert brain.sessions == 2
