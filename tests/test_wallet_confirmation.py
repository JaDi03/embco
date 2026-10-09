from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from agent.guardrails.controls import Outcome, PayeeWallet, WalletChallenge, WalletProof
from agent.guardrails.rules import Action, DecisionEngine, PolicyConfig, Verdict, apply_answers
from agent.memory import (
    JournalError,
    SqliteJournal,
    answer_ask,
    answers_for,
    issue_wallet_challenges,
    remember,
    submit_wallet_signature,
)
from services.signing import recover_signer, typed_data
from support import WALLET_A, FakeLedger, context_for

SUPPLIER_KEY = Account.create()  # the supplier's new wallet, fresh on every run
INTRUDER_KEY = Account.create()
NEW_WALLET = SUPPLIER_KEY.address
MONDAY = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
POLICY = PolicyConfig(max_per_payment=Decimal(5000), weekly_budget=Decimal(2500))


def changed_ledger(wallet=NEW_WALLET) -> FakeLedger:
    ledger = FakeLedger()
    ledger.supplier = ledger.supplier.model_copy(update={"wallet_address": wallet})
    return ledger


def proof(wallet=NEW_WALLET) -> WalletProof:
    return WalletProof(supplier="S", wallet=wallet, nonce="n", signature="0x", signed_at=MONDAY)


def challenge(wallet=NEW_WALLET) -> WalletChallenge:
    return WalletChallenge(supplier="S", wallet=wallet, payer="TEST Shop", nonce="abc123",
                           issued_at=MONDAY, expires_at=MONDAY + timedelta(days=7))


def sign(c: WalletChallenge, key=SUPPLIER_KEY, chain_id=5042002) -> str:
    signable = encode_typed_data(full_message=typed_data(c, chain_id))
    return "0x" + key.sign_message(signable).signature.hex().removeprefix("0x")


def check(ledger, wallet_proof=None):
    return PayeeWallet().check(replace(context_for(ledger), wallet_proof=wallet_proof))


# The control


def test_a_changed_wallet_waits_for_the_supplier_signature():
    finding = check(changed_ledger())
    assert finding.outcome is Outcome.HOLD
    assert "wallet changed since the last payment" in finding.reason
    assert finding.reason.endswith("waiting for the supplier to sign the wallet challenge")


def test_a_signed_wallet_asks_the_owner_to_approve():
    finding = check(changed_ledger(), proof())
    assert finding.outcome is Outcome.ASK
    assert "the supplier proved it controls the new wallet on 2026-10-12" in finding.reason
    assert "Approve only if the supplier asked for this change" in finding.reason


def test_a_proof_for_another_wallet_does_not_count():
    assert check(changed_ledger(), proof(INTRUDER_KEY.address)).outcome is Outcome.HOLD


def test_proof_comparison_ignores_letter_case():
    assert check(changed_ledger(), proof(NEW_WALLET.lower())).outcome is Outcome.ASK


def test_the_first_wallet_of_a_supplier_follows_the_same_path():
    ledger = changed_ledger()
    ledger.payments = []
    assert check(ledger).reason.startswith("first payment to this supplier")
    assert check(ledger).outcome is Outcome.HOLD
    assert check(ledger, proof()).outcome is Outcome.ASK


# The signature


def test_the_signer_of_the_challenge_is_recovered():
    assert recover_signer(challenge(), sign(challenge())) == NEW_WALLET


def test_a_signature_does_not_carry_over_to_another_wallet_chain_or_garbage():
    signature = sign(challenge())
    other = replace(challenge(), wallet=INTRUDER_KEY.address)
    assert recover_signer(other, signature) != INTRUDER_KEY.address
    assert recover_signer(challenge(), sign(challenge(), chain_id=1)) != NEW_WALLET
    assert recover_signer(challenge(), "0x1234") is None


def test_the_supplier_sees_readable_fields():
    message = typed_data(challenge())["message"]
    assert message["statement"] == "S asks TEST Shop to pay it to this wallet."
    assert message["wallet"] == NEW_WALLET
    assert message["expiresAt"] == "2026-10-19T09:00:00+00:00"


# The challenge exchange


@pytest.fixture
def journal(tmp_path):
    with SqliteJournal(tmp_path / "journal.sqlite3") as j:
        yield j


def issue(journal, ledger, at=MONDAY):
    return issue_wallet_challenges(journal, ledger, ["S"], "TEST Shop", at=at)


def test_a_challenge_is_issued_only_when_a_wallet_needs_proof(journal):
    assert issue(journal, FakeLedger()) == []
    issued = issue(journal, changed_ledger())
    assert [c.wallet for c in issued] == [NEW_WALLET]
    assert issue(journal, changed_ledger(), MONDAY + timedelta(days=1)) == issued


def test_an_expired_challenge_is_replaced(journal):
    first = issue(journal, changed_ledger())[0]
    second = issue(journal, changed_ledger(), MONDAY + timedelta(days=8))[0]
    assert second.nonce != first.nonce


def test_the_right_signature_is_recorded_as_proof(tmp_path):
    path = tmp_path / "journal.sqlite3"
    ledger = changed_ledger()
    with SqliteJournal(path) as journal:
        c = issue(journal, ledger)[0]
        saved = submit_wallet_signature(journal, ledger, "S", sign(c), at=MONDAY)
        assert issue(journal, ledger) == []
    with SqliteJournal(path) as journal:
        assert journal.latest_wallet_proof("S") == saved
        journal.verify()


def test_a_signature_from_another_wallet_is_refused(journal):
    ledger = changed_ledger()
    c = issue(journal, ledger)[0]
    with pytest.raises(JournalError, match="not made by the wallet on file"):
        submit_wallet_signature(journal, ledger, "S", sign(c, INTRUDER_KEY), at=MONDAY)
    assert journal.latest_wallet_proof("S") is None


def test_a_late_signature_or_a_moved_wallet_is_refused(journal):
    ledger = changed_ledger()
    with pytest.raises(JournalError, match="no wallet challenge"):
        submit_wallet_signature(journal, ledger, "S", "0x", at=MONDAY)
    c = issue(journal, ledger)[0]
    with pytest.raises(JournalError, match="expired"):
        submit_wallet_signature(journal, ledger, "S", sign(c), at=MONDAY + timedelta(days=8))
    moved = changed_ledger(INTRUDER_KEY.address)
    with pytest.raises(JournalError, match="changed after the challenge"):
        submit_wallet_signature(journal, moved, "S", sign(c), at=MONDAY)


def test_from_hold_to_pay_with_signature_and_owner_approval(journal):
    ledger = changed_ledger()

    def run():
        raw = DecisionEngine(ledger, POLICY, proofs=journal).decide_all()
        decisions = apply_answers(raw, answers_for(journal, raw))
        remember(journal, decisions, POLICY, at=MONDAY)
        return decisions[0]

    assert run().action is Action.HOLD
    c = issue(journal, ledger)[0]
    submit_wallet_signature(journal, ledger, "S", sign(c), at=MONDAY)
    assert run().action is Action.ASK
    answer_ask(journal, "PINV-1", Verdict.APPROVE, "owner", at=MONDAY)
    paid = run()
    assert paid.action is Action.PAY
    assert paid.reasons[0] == "approved by owner on 2026-10-12"
    ledger.supplier = ledger.supplier.model_copy(update={"wallet_address": WALLET_A[:-1] + "f"})
    assert run().action is Action.HOLD
    journal.verify()
