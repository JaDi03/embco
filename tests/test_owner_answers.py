import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from agent.guardrails.controls import Finding, Outcome
from agent.guardrails.rules import (
    Action,
    Decision,
    OwnerAnswer,
    PolicyConfig,
    Verdict,
    apply_answer,
    apply_answers,
    fingerprint,
)
from agent.memory import ChangeKind, JournalError, SqliteJournal, answer_ask, answers_for, remember

POLICY = PolicyConfig(max_per_payment=Decimal(500), weekly_budget=Decimal(2500))
MONDAY = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
NOON = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
TUESDAY = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def asked(amount="1000"):
    reason = f"amount {amount} NGN is above the per-payment limit of 500"
    return Decision(
        invoice="PINV-1", supplier="S", amount=Decimal(amount), due_date=date(2026, 10, 10),
        action=Action.ASK, reasons=(reason,),
        findings=(Finding("payment_limit", Outcome.ASK, reason),),
    )


def answer(decision, verdict=Verdict.APPROVE, note=""):
    return OwnerAnswer("PINV-1", fingerprint(decision), verdict, "owner", MONDAY, note)


@pytest.fixture
def journal(tmp_path):
    with SqliteJournal(tmp_path / "journal.sqlite3") as j:
        yield j


def test_an_approved_ask_becomes_pay_and_says_who_approved():
    decided = apply_answer(asked(), answer(asked()))
    assert decided.action is Action.PAY
    assert decided.reasons[0] == "approved by owner on 2026-10-12"
    assert decided.reasons[1:] == asked().reasons


def test_a_rejected_ask_becomes_hold_with_the_owner_note():
    decided = apply_answer(asked(), answer(asked(), Verdict.REJECT, "too expensive"))
    assert decided.action is Action.HOLD
    assert decided.reasons[0] == "rejected by owner on 2026-10-12: too expensive"


def test_an_answer_about_an_earlier_version_does_not_count():
    old = answer(asked("1000"))
    assert apply_answer(asked("1200"), old).action is Action.ASK


def test_the_owner_cannot_turn_a_hold_into_a_payment():
    held = Decision(
        invoice="PINV-1", supplier="S", amount=Decimal(1000), due_date=None, action=Action.HOLD,
        reasons=("wallet changed since the last payment",), findings=(),
    )
    assert apply_answer(held, answer(held)).action is Action.HOLD


def test_only_an_invoice_waiting_for_an_answer_can_be_answered(journal):
    with pytest.raises(JournalError, match="no decision"):
        answer_ask(journal, "PINV-1", Verdict.APPROVE, "owner")
    paid = Decision(
        invoice="PINV-1", supplier="S", amount=Decimal(100), due_date=None, action=Action.PAY,
        reasons=("all 6 controls passed",), findings=(),
    )
    remember(journal, [paid], POLICY, at=MONDAY)
    with pytest.raises(JournalError, match="not waiting"):
        answer_ask(journal, "PINV-1", Verdict.APPROVE, "owner")


def test_an_invoice_that_left_the_unpaid_list_cannot_be_answered(journal):
    remember(journal, [asked()], POLICY, at=MONDAY)
    remember(journal, [], POLICY, at=TUESDAY)
    with pytest.raises(JournalError, match="no longer unpaid"):
        answer_ask(journal, "PINV-1", Verdict.APPROVE, "owner")


def test_an_answer_needs_a_name(journal):
    remember(journal, [asked()], POLICY, at=MONDAY)
    with pytest.raises(JournalError, match="name"):
        answer_ask(journal, "PINV-1", Verdict.APPROVE, "  ")


def run(journal, raw, at):
    decisions = apply_answers(raw, answers_for(journal, raw))
    return remember(journal, decisions, POLICY, at=at)


def test_the_agent_remembers_the_answer_on_the_next_runs(journal):
    run(journal, [asked()], MONDAY)
    answer_ask(journal, "PINV-1", Verdict.APPROVE, "owner", at=NOON)
    change = run(journal, [asked()], TUESDAY).changes[0]
    assert change.decision.action is Action.PAY
    assert change.note == "was ASK in run 1, now PAY"
    assert run(journal, [asked()], TUESDAY).changes[0].kind is ChangeKind.SAME


def test_a_changed_invoice_is_asked_again_after_an_approval(journal):
    run(journal, [asked("1000")], MONDAY)
    answer_ask(journal, "PINV-1", Verdict.APPROVE, "owner", at=NOON)
    change = run(journal, [asked("1200")], TUESDAY).changes[0]
    assert change.decision.action is Action.ASK
    assert change.kind is ChangeKind.CHANGED


def test_the_latest_answer_wins(journal):
    run(journal, [asked()], MONDAY)
    answer_ask(journal, "PINV-1", Verdict.REJECT, "owner", at=MONDAY)
    answer_ask(journal, "PINV-1", Verdict.APPROVE, "owner", note="checked by phone", at=NOON)
    assert run(journal, [asked()], TUESDAY).changes[0].decision.action is Action.PAY


def test_answers_survive_reopening_and_cannot_be_edited(tmp_path):
    path = tmp_path / "journal.sqlite3"
    with SqliteJournal(path) as journal:
        remember(journal, [asked()], POLICY, at=MONDAY)
        answer_ask(journal, "PINV-1", Verdict.REJECT, "owner", note="too expensive", at=NOON)
    with SqliteJournal(path) as journal:
        saved = journal.latest_answer("PINV-1")
        journal.verify()
    assert saved == OwnerAnswer(
        "PINV-1", fingerprint(asked()), Verdict.REJECT, "owner", NOON, "too expensive"
    )
    raw = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            raw.execute("UPDATE entries SET body = '{}' WHERE kind = 'ANSWER'")
        raw.execute("DROP TRIGGER entries_no_update")
        raw.execute("UPDATE entries SET body = replace(body, 'REJECT', 'APPROVE')")
        raw.commit()
    finally:
        raw.close()
    with SqliteJournal(path) as journal, pytest.raises(JournalError, match="ANSWER PINV-1"):
        journal.verify()
