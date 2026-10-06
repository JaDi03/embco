"""Proof that the supplier controls the wallet the ERP holds, obtained with a signature challenge.

A proof says the address is the supplier's and is written correctly. It does not say who asked
for the change: that stays with a person, who approves the first payment to the new wallet.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class WalletChallenge:
    supplier: str
    wallet: str
    payer: str
    nonce: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class WalletProof:
    supplier: str
    wallet: str
    nonce: str
    signature: str
    signed_at: datetime


class WalletProofSource(Protocol):
    def latest_wallet_proof(self, supplier: str) -> WalletProof | None: ...
