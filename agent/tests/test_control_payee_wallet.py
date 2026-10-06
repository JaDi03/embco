from embco.controls import Outcome, PayeeWallet
from support import WALLET_A, WALLET_B, FakeLedger, context_for


def check(ledger: FakeLedger):
    return PayeeWallet().check(context_for(ledger))


def test_passes_when_wallet_matches_the_last_one_paid():
    assert check(FakeLedger()).outcome is Outcome.PASS


def test_comparison_ignores_letter_case():
    ledger = FakeLedger()
    shouting = "0x" + WALLET_A[2:].upper()
    ledger.supplier = ledger.supplier.model_copy(update={"wallet_address": shouting})
    assert check(ledger).outcome is Outcome.PASS


def test_holds_when_the_wallet_changed_since_the_last_payment():
    ledger = FakeLedger()
    ledger.supplier = ledger.supplier.model_copy(update={"wallet_address": WALLET_B})
    finding = check(ledger)
    assert finding.outcome is Outcome.HOLD
    assert "wallet changed" in finding.reason


def test_holds_when_there_is_no_wallet():
    ledger = FakeLedger()
    ledger.supplier = ledger.supplier.model_copy(update={"wallet_address": None})
    assert check(ledger).outcome is Outcome.HOLD


def test_holds_when_the_wallet_is_not_a_valid_address():
    ledger = FakeLedger()
    ledger.supplier = ledger.supplier.model_copy(update={"wallet_address": "0x123"})
    assert check(ledger).outcome is Outcome.HOLD


def test_holds_the_first_payment_until_the_wallet_is_proven():
    ledger = FakeLedger()
    ledger.payments = []
    assert check(ledger).outcome is Outcome.HOLD
