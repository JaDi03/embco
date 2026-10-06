from decimal import Decimal

from embco.controls import Outcome, PaymentLimit, PriceAnomaly
from support import FakeLedger, context_for, make_invoice


def with_rate(rate: str) -> FakeLedger:
    ledger = FakeLedger()
    ledger.pending = [make_invoice(rate=rate)]
    return ledger


def test_a_price_far_above_the_habitual_one_needs_the_owner():
    finding = PriceAnomaly().check(context_for(with_rate("135")))
    assert finding.outcome is Outcome.ASK
    assert "35% above the habitual 100" in finding.reason


def test_a_small_increase_passes():
    assert PriceAnomaly().check(context_for(with_rate("110"))).outcome is Outcome.PASS


def test_without_history_there_is_nothing_to_compare():
    ledger = with_rate("500")
    ledger.history = []
    assert PriceAnomaly().check(context_for(ledger)).outcome is Outcome.PASS


def test_unpaid_invoices_do_not_count_as_history():
    ledger = with_rate("135")
    ledger.history = [make_invoice("PINV-0", "H-0", rate="135")]  # still unpaid
    assert PriceAnomaly().check(context_for(ledger)).outcome is Outcome.PASS


def test_amount_above_the_limit_needs_the_owner():
    finding = PaymentLimit(Decimal(500)).check(context_for(FakeLedger()))
    assert finding.outcome is Outcome.ASK
    assert "above the per-payment limit of 500" in finding.reason


def test_amount_equal_to_the_limit_passes():
    assert PaymentLimit(Decimal(1000)).check(context_for(FakeLedger())).outcome is Outcome.PASS
