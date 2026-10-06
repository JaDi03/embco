import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from embco.controls import Finding, Outcome
from embco.decision import Action, Decision, DecisionEngine, PolicyConfig, fingerprint
from embco.journal import ChangeKind, JournalError, SqliteJournal, remember
from support import FakeLedger

POLICY = PolicyConfig(max_per_payment=Decimal(5000), weekly_budget=Decimal(2500))
MONDAY = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
TUESDAY = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def decision(action=Action.HOLD, reasons=("COLA: invoiced 12 but received 10",), amount="1000"):
    finding = Finding("three_way_match", Outcome.HOLD, reasons[0])
    return Decision(
        invoice="PINV-1", supplier="S", amount=Decimal(amount), due_date=date(2026, 10, 10),
        action=action, reasons=reasons, findings=(finding,),
    )


@pytest.fixture
def journal(tmp_path):
    with SqliteJournal(tmp_path / "journal.sqlite3") as j:
        yield j


def test_the_first_run_sees_everything_as_new(journal):
    decisions = DecisionEngine(FakeLedger(), POLICY).decide_all()
    memory = remember(journal, decisions, POLICY, at=MONDAY)
    assert memory.run_id == 1
    assert [c.kind for c in memory.changes] == [ChangeKind.NEW]


def test_a_second_run_with_nothing_new_says_so(journal):
    remember(journal, [decision()], POLICY, at=MONDAY)
    change = remember(journal, [decision()], POLICY, at=TUESDAY).changes[0]
    assert change.kind is ChangeKind.SAME
    assert change.note == "same as run 1: HOLD"


def test_a_new_action_is_reported_against_the_last_one(journal):
    remember(journal, [decision()], POLICY, at=MONDAY)
    paid = decision(Action.PAY, ("all 6 controls passed",))
    change = remember(journal, [paid], POLICY, at=TUESDAY).changes[0]
    assert change.kind is ChangeKind.CHANGED
    assert change.note == "was HOLD in run 1, now PAY"


def test_same_action_with_new_reasons_is_still_a_change(journal):
    remember(journal, [decision()], POLICY, at=MONDAY)
    other = decision(reasons=("COLA: invoiced 15 but received 10",))
    change = remember(journal, [other], POLICY, at=TUESDAY).changes[0]
    assert change.kind is ChangeKind.CHANGED
    assert change.note.startswith("still HOLD")


def test_memory_survives_closing_and_reopening_the_file(tmp_path):
    path = tmp_path / "journal.sqlite3"
    with SqliteJournal(path) as first:
        remember(first, [decision()], POLICY, at=MONDAY)
    with SqliteJournal(path) as second:
        entry = second.last_entry("PINV-1")
    assert entry is not None
    assert entry.run_id == 1
    assert entry.recorded_at == MONDAY


def test_entries_come_back_exactly_as_they_were_written(journal):
    written = decision(amount="249.995")
    remember(journal, [written], POLICY, at=MONDAY)
    entry = journal.last_entry("PINV-1")
    assert entry.amount == Decimal("249.995")
    assert entry.due_date == date(2026, 10, 10)
    assert entry.action is Action.HOLD
    assert entry.reasons == written.reasons
    assert entry.findings == written.findings


def test_history_keeps_every_decision_oldest_first(journal):
    remember(journal, [decision()], POLICY, at=MONDAY)
    remember(journal, [decision(Action.PAY, ("all 6 controls passed",))], POLICY, at=TUESDAY)
    assert [e.action for e in journal.history("PINV-1")] == [Action.HOLD, Action.PAY]
    assert journal.last_entry("PINV-404") is None


def test_an_unchanged_decision_is_not_stored_again_but_the_run_is(tmp_path):
    path = tmp_path / "journal.sqlite3"
    with SqliteJournal(path) as journal:
        for day in range(1, 6):
            memory = remember(journal, [decision()], POLICY, at=MONDAY.replace(day=day))
        assert memory.run_id == 5
        assert len(journal.history("PINV-1")) == 1
        assert journal.last_entry("PINV-1").run_id == 1
    assert count(path, "RUN") == 5
    assert count(path, "DECISION") == 1


def test_the_policy_is_stored_only_when_it_changes(tmp_path):
    path = tmp_path / "journal.sqlite3"
    stricter = PolicyConfig(max_per_payment=Decimal(100), weekly_budget=Decimal(2500))
    with SqliteJournal(path) as journal:
        for policy in (POLICY, POLICY, stricter, stricter):
            remember(journal, [decision()], policy, at=MONDAY)
    assert count(path, "POLICY") == 2


def test_an_invoice_that_leaves_the_unpaid_list_is_closed(journal):
    remember(journal, [decision()], POLICY, at=MONDAY)
    memory = remember(journal, [], POLICY, at=TUESDAY)
    assert memory.closed == ("PINV-1",)
    assert journal.open_invoices() == set()
    assert remember(journal, [], POLICY, at=TUESDAY).closed == ()


def test_an_invoice_that_comes_back_is_reported_and_reopened(journal):
    remember(journal, [decision()], POLICY, at=MONDAY)
    remember(journal, [], POLICY, at=MONDAY)
    change = remember(journal, [decision()], POLICY, at=TUESDAY).changes[0]
    assert change.kind is ChangeKind.CHANGED
    assert change.note == "back among the unpaid invoices; it was HOLD in run 1"
    assert journal.open_invoices() == {"PINV-1"}


def test_the_journal_rejects_updates_and_deletes(tmp_path):
    path = tmp_path / "journal.sqlite3"
    with SqliteJournal(path) as journal:
        remember(journal, [decision()], POLICY, at=MONDAY)
    raw = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            raw.execute("UPDATE entries SET body = '{}'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            raw.execute("DELETE FROM entries")
    finally:
        raw.close()


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE entries SET body = replace(body, 'HOLD', 'PAY') WHERE kind = 'DECISION'",
        "DELETE FROM entries WHERE kind = 'RUN' AND run = 2",
    ],
)
def test_an_edit_or_removal_made_around_the_triggers_is_caught(tmp_path, tamper):
    path = tmp_path / "journal.sqlite3"
    with SqliteJournal(path) as journal:
        for day in (12, 13, 14):
            remember(journal, [decision()], POLICY, at=MONDAY.replace(day=day))
        journal.verify()
    raw = sqlite3.connect(path)
    try:
        raw.execute("DROP TRIGGER entries_no_update")
        raw.execute("DROP TRIGGER entries_no_delete")
        raw.execute(tamper)
        raw.commit()
    finally:
        raw.close()
    with SqliteJournal(path) as journal, pytest.raises(JournalError, match="was altered"):
        journal.verify()


def test_a_journal_from_an_unknown_format_is_refused(tmp_path):
    path = tmp_path / "journal.sqlite3"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 99")
    raw.close()
    with pytest.raises(JournalError, match="format 99"):
        SqliteJournal(path)


def test_timestamps_without_a_timezone_are_refused(journal):
    with pytest.raises(JournalError):
        remember(journal, [decision()], POLICY, at=datetime(2026, 10, 12, 9, 0))


def count(path, kind):
    raw = sqlite3.connect(path)
    try:
        return raw.execute("SELECT COUNT(*) FROM entries WHERE kind = ?", (kind,)).fetchone()[0]
    finally:
        raw.close()


def test_the_fingerprint_ignores_how_the_amount_is_written():
    assert fingerprint(decision(amount="6")) == fingerprint(decision(amount="6.000000"))
    assert fingerprint(decision(amount="6")) != fingerprint(decision(amount="7"))
