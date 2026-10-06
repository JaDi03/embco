"""The wallet signature challenge: the agent issues it, the supplier signs, the agent verifies.

A valid signature proves the supplier controls the wallet the ERP holds, written exactly right.
The person who approves the first payment decides whether the supplier really asked for it.
"""

import secrets
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from embco.controls import WalletChallenge, WalletProof
from embco.controls.payee_wallet import last_paid_wallet, needs_proof
from embco.journal.base import DecisionJournal, JournalError
from embco.ledger import LedgerAdapter
from embco.signing import ARC_TESTNET_CHAIN_ID, recover_signer

CHALLENGE_TTL = timedelta(days=7)


def issue_wallet_challenges(
    journal: DecisionJournal,
    ledger: LedgerAdapter,
    suppliers: Iterable[str],
    payer: str,
    *,
    at: datetime | None = None,
    ttl: timedelta = CHALLENGE_TTL,
) -> list[WalletChallenge]:
    """Challenges waiting for a signature, one per supplier whose wallet still needs proof.

    A pending challenge for the same wallet is reused; an expired one, or one for an older
    wallet, is replaced by a new one.
    """
    now = at or datetime.now(UTC)
    pending = []
    for name in sorted(set(suppliers)):
        supplier = ledger.get_supplier(name)
        last_paid = last_paid_wallet(ledger.list_payments(name))
        if not needs_proof(supplier, last_paid, journal.latest_wallet_proof(name)):
            continue
        wallet = supplier.wallet_address or ""
        open_one = journal.latest_wallet_challenge(name)
        if open_one and open_one.wallet.lower() == wallet.lower() and open_one.expires_at > now:
            pending.append(open_one)
            continue
        challenge = WalletChallenge(
            supplier=name,
            wallet=wallet,
            payer=payer,
            nonce=secrets.token_hex(16),
            issued_at=now,
            expires_at=now + ttl,
        )
        journal.record_wallet_challenge(challenge)
        pending.append(challenge)
    return pending


def submit_wallet_signature(
    journal: DecisionJournal,
    ledger: LedgerAdapter,
    supplier: str,
    signature: str,
    *,
    at: datetime | None = None,
    chain_id: int = ARC_TESTNET_CHAIN_ID,
) -> WalletProof:
    """Verify the supplier's signature on its latest challenge and record the proof."""
    now = at or datetime.now(UTC)
    challenge = journal.latest_wallet_challenge(supplier)
    if challenge is None:
        raise JournalError(f"there is no wallet challenge for {supplier}")
    if challenge.expires_at <= now:
        raise JournalError(f"the wallet challenge for {supplier} expired; a new one will be issued")
    current = ledger.get_supplier(supplier).wallet_address or ""
    if current.lower() != challenge.wallet.lower():
        raise JournalError(f"the wallet of {supplier} in the ERP changed after the challenge")
    signer = recover_signer(challenge, signature, chain_id)
    if signer is None or signer.lower() != challenge.wallet.lower():
        raise JournalError(f"the signature was not made by the wallet on file for {supplier}")
    proof = WalletProof(
        supplier=supplier,
        wallet=challenge.wallet,
        nonce=challenge.nonce,
        signature=signature,
        signed_at=now,
    )
    journal.record_wallet_proof(proof)
    return proof
