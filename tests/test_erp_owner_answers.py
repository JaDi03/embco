import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from agent.guardrails.rules import Action, PolicyConfig, Verdict
from agent.memory import SqliteJournal
from agent.reflexes.cycle import run_cycle
from services.erp import ErpnextAdapter
from services.erp.models import OwnerMark
from support import FakeLedger, asking_ledger, make_invoice

OWNER = "owner@shop.test"
POLICY = PolicyConfig(max_per_payment=Decimal(5000), weekly_budget=Decimal(5000))
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def mark(verdict="Approve", by=OWNER, change="v-1", note=""):
    return OwnerMark(verdict=verdict, note=note, set_by=by, set_at=datetime(2026, 10, 12, 10),
                     change_id=change)


@pytest.fixture
def journal():
    with SqliteJournal(":memory:") as j:
        yield j


def cycle(ledger, journal, minutes=0):
    return run_cycle(ledger, journal, POLICY, "TEST Shop", at=NOW + timedelta(minutes=minutes),
                     owners=(OWNER,))


def asked_once(journal) -> FakeLedger:
    """The supplier's new wallet is proven: the owner is asked about the first payment."""
    ledger = asking_ledger(journal)
    assert [d.action for d in cycle(ledger, journal).decisions] == [Action.ASK]
    return ledger


def test_an_owner_mark_in_the_erp_answers_the_question(journal):
    ledger = asked_once(journal)
    ledger.marks = {"PINV-1": mark(note="called the supplier")}
    [decision] = cycle(ledger, journal, 15).decisions
    assert decision.action is Action.PAY and decision.reasons[0].startswith(f"approved by {OWNER}")
    saved = journal.latest_answer("PINV-1")
    assert saved.verdict is Verdict.APPROVE and "called the supplier" in saved.note
    [again] = cycle(ledger, journal, 30).decisions  # remembered, not read again as new
    assert again.action is Action.PAY


def test_a_reject_mark_holds_the_invoice(journal):
    ledger = asked_once(journal)
    ledger.marks = {"PINV-1": mark("Reject")}
    [decision] = cycle(ledger, journal, 15).decisions
    assert decision.action is Action.HOLD


@pytest.mark.parametrize("bad", [mark(by="clerk@shop.test"), mark("Maybe")])
def test_a_mark_by_someone_else_or_unclear_answers_nothing(journal, bad):
    ledger = asked_once(journal)
    ledger.marks = {"PINV-1": bad}
    assert [d.action for d in cycle(ledger, journal, 15).decisions] == [Action.ASK]
    assert journal.latest_answer("PINV-1") is None


def test_a_mark_needs_a_question_the_agent_already_showed(journal):
    ledger = asking_ledger(journal)
    ledger.marks = {"PINV-1": mark()}
    assert [d.action for d in cycle(ledger, journal).decisions] == [Action.ASK]


def test_without_owner_accounts_marks_are_not_read(journal):
    ledger = asked_once(journal)
    ledger.marks = {"PINV-1": mark()}
    report = run_cycle(ledger, journal, POLICY, "TEST Shop", at=NOW + timedelta(minutes=15))
    assert [d.action for d in report.decisions] == [Action.ASK]


def test_one_mark_answers_one_question(journal):
    ledger = asked_once(journal)
    ledger.marks = {"PINV-1": mark("Reject")}
    cycle(ledger, journal, 15)
    # the question changes (the amount due changed): the old mark does not answer it
    ledger.pending = [make_invoice(outstanding="900")]
    assert [d.action for d in cycle(ledger, journal, 30).decisions] == [Action.ASK]
    # the old Reject is still on the invoice: it does not hold the new question
    assert [d.action for d in cycle(ledger, journal, 40).decisions] == [Action.ASK]
    ledger.marks = {"PINV-1": mark("Approve", change="v-2")}  # the owner writes it again
    assert [d.action for d in cycle(ledger, journal, 45).decisions] == [Action.PAY]


def adapter_for(doc, versions):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("get_docinfo"):
            assert request.url.params["doctype"] == "Purchase Invoice"
            return httpx.Response(200, json={"docinfo": {"versions": versions}})
        assert request.url.path == "/api/resource/Purchase Invoice/PINV-1"
        return httpx.Response(200, json={"data": doc})

    return ErpnextAdapter("https://erp.test", "k", "s",
                          client=httpx.Client(transport=httpx.MockTransport(handler)))


def version(name, by, when, old, new):
    return {"name": name, "owner": by, "creation": when,
            "data": json.dumps({"changed": [["custom_owner_answer", old, new]]})}


def test_the_adapter_names_who_wrote_the_current_mark():
    doc = {"name": "PINV-1", "custom_owner_answer": "Approve", "custom_owner_note": " ok "}
    found = adapter_for(doc, [
        version("v-1", "clerk@shop.test", "2026-10-12 08:00:00", None, "Reject"),
        version("v-2", OWNER, "2026-10-12 10:00:00", "Reject", "Approve"),
    ]).owner_mark("PINV-1")
    assert (found.verdict, found.set_by, found.change_id, found.note) == (
        "Approve", OWNER, "v-2", "ok")


def test_a_mark_without_a_recorded_change_or_empty_is_ignored():
    assert adapter_for({"custom_owner_answer": "Approve"}, []).owner_mark("PINV-1") is None
    assert adapter_for({"custom_owner_answer": ""}, []).owner_mark("PINV-1") is None
