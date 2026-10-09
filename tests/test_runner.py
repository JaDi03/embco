import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agent.guardrails.rules import Action, PolicyConfig
from agent.memory import JournalError, SqliteJournal
from agent.reflexes.cycle import run_cycle
from agent.reflexes.report import format_report
from agent.reflexes.watch import watch
from services.erp import LedgerError
from support import FakeLedger

POLICY = PolicyConfig(max_per_payment=Decimal(5000), weekly_budget=Decimal(2500))
MONDAY = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
NEW_WALLET = "0x" + "c3" * 20


@pytest.fixture
def journal(tmp_path):
    with SqliteJournal(tmp_path / "journal.sqlite3") as j:
        yield j


def cycle(ledger, journal, at=MONDAY):
    return run_cycle(ledger, journal, POLICY, "TEST Shop", at=at)


def test_a_cycle_decides_remembers_and_plans(journal):
    report = cycle(FakeLedger(), journal)
    assert report.run_id == 1
    assert [d.action for d in report.decisions] == [Action.PAY]
    assert [d.invoice for d in report.plan.pay_now] == ["PINV-1"]
    assert report.plan.budget_left == Decimal(1500)
    assert cycle(FakeLedger(), journal).run_id == 2


def test_a_cycle_asks_the_supplier_to_sign_a_new_wallet(journal):
    ledger = FakeLedger()
    ledger.supplier = ledger.supplier.model_copy(update={"wallet_address": NEW_WALLET})
    report = cycle(ledger, journal)
    assert [c.wallet for c in report.challenges] == [NEW_WALLET]
    assert "waiting for S to sign for wallet" in format_report(report)


def test_the_report_shows_only_what_changed_unless_verbose(journal):
    cycle(FakeLedger(), journal)
    report = cycle(FakeLedger(), journal, MONDAY + timedelta(hours=1))
    quiet = format_report(report)
    assert quiet.startswith("run 2 at 2026-10-12 10:00 UTC: 1 unpaid (0 new, 0 changed, 1 same")
    assert "PINV-1" not in quiet
    assert "plan: pay now 1 (1000), deferred 0, held 0, ask 0; budget left 1500" in quiet
    assert "- all 6 controls passed" in format_report(report, verbose=True)


def test_a_tampered_memory_stops_the_agent_before_it_decides(tmp_path):
    path = tmp_path / "journal.sqlite3"
    with SqliteJournal(path) as journal:
        cycle(FakeLedger(), journal)
    raw = sqlite3.connect(path)
    raw.execute("DROP TRIGGER entries_no_update")
    raw.execute("UPDATE entries SET body = replace(body, 'PAY', 'HOLD') WHERE kind = 'DECISION'")
    raw.commit()
    raw.close()
    with SqliteJournal(path) as journal:
        with pytest.raises(JournalError):
            cycle(FakeLedger(), journal)
        assert journal.last_entry("PINV-1").run_id == 1


def test_watch_survives_an_erp_outage_and_waits_between_cycles():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) == 2:
            raise LedgerError("ERPNext request failed: ConnectError")

    ok = watch(flaky, timedelta(minutes=15), cycles=3, sleep=sleeps.append)
    assert (len(calls), ok) == (3, 2)
    assert sleeps == [900.0, 900.0]


def test_watch_stops_on_a_memory_error():
    def broken():
        raise JournalError("journal entry 3 (DECISION PINV-1) was altered")

    with pytest.raises(JournalError):
        watch(broken, timedelta(minutes=1), cycles=5, sleep=lambda _: None)
