from datetime import date

from agent.guardrails.controls import DuplicateInvoice, Outcome
from support import FakeLedger, context_for, make_invoice


def ledger_with(second_bill_no: str, second_posted: date = date(2026, 10, 1)) -> FakeLedger:
    ledger = FakeLedger()
    ledger.pending = [
        make_invoice("PINV-1", "INV-22"),
        make_invoice("PINV-2", second_bill_no, posting_date=second_posted),
    ]
    return ledger


def test_the_later_invoice_with_the_same_number_is_held():
    ledger = ledger_with("INV-22")
    finding = DuplicateInvoice().check(context_for(ledger, ledger.pending[1]))
    assert finding.outcome is Outcome.HOLD
    assert "PINV-1" in finding.reason


def test_the_original_is_not_held():
    ledger = ledger_with("INV-22")
    assert DuplicateInvoice().check(context_for(ledger, ledger.pending[0])).outcome is Outcome.PASS


def test_a_different_way_of_typing_the_number_is_still_caught():
    ledger = ledger_with("inv 22")
    finding = DuplicateInvoice().check(context_for(ledger, ledger.pending[1]))
    assert finding.outcome is Outcome.HOLD


def test_a_different_number_passes():
    ledger = ledger_with("INV-23")
    assert DuplicateInvoice().check(context_for(ledger, ledger.pending[1])).outcome is Outcome.PASS


def test_an_invoice_without_a_number_cannot_be_compared():
    ledger = FakeLedger()
    ledger.pending = [make_invoice(bill_no=None)]
    assert DuplicateInvoice().check(context_for(ledger)).outcome is Outcome.PASS
