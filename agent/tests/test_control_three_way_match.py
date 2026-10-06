from decimal import Decimal

from embco.controls import Outcome, ThreeWayMatch
from support import FakeLedger, context_for, make_invoice


def test_passes_when_order_receipt_and_invoice_agree():
    finding = ThreeWayMatch().check(context_for(FakeLedger()))
    assert finding.outcome is Outcome.PASS


def test_holds_when_invoiced_more_than_received():
    ledger = FakeLedger()
    receipt = ledger.receipts["PR-1"]
    short = receipt.lines[0].model_copy(update={"qty": Decimal(8)})
    ledger.receipts["PR-1"] = receipt.model_copy(update={"lines": (short,)})
    finding = ThreeWayMatch().check(context_for(ledger))
    assert finding.outcome is Outcome.HOLD
    assert "invoiced 10 but received 8" in finding.reason


def test_holds_when_rate_differs_from_the_order():
    ledger = FakeLedger()
    ledger.pending = [make_invoice(rate="120")]
    finding = ThreeWayMatch().check(context_for(ledger))
    assert finding.outcome is Outcome.HOLD
    assert "rate 120 differs from ordered 100" in finding.reason


def test_rate_difference_inside_the_declared_tolerance_passes():
    ledger = FakeLedger()
    ledger.pending = [make_invoice(rate="101")]
    finding = ThreeWayMatch(rate_tolerance=Decimal(2)).check(context_for(ledger))
    assert finding.outcome is Outcome.PASS


def test_holds_when_the_invoice_is_not_linked_to_order_and_receipt():
    ledger = FakeLedger()
    bare = make_invoice().lines[0].model_copy(update={"purchase_order": None})
    ledger.pending = [make_invoice(lines=(bare,))]
    finding = ThreeWayMatch().check(context_for(ledger))
    assert finding.outcome is Outcome.HOLD
    assert "not linked" in finding.reason
