"""How a supplier signs in to its page: with the wallet it is paid to, proven by a signature.

There is no link and no password. Any wallet can sign in, and it sees only the entries whose
wallet on file in a shop's ERP is that wallet. A nonce works once and for a few minutes; a
session lives in memory, so a restart only asks the supplier to sign again.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data

from services.hub.auth import NONCE_LIFETIME, SESSION_LIFETIME, AuthError
from services.signing.wallet_ownership import ARC_TESTNET_CHAIN_ID

MAX_OPEN_REQUESTS = 10_000  # sign-in requests are unauthenticated: cap what they can hold
STATEMENT = "Sign in to see what shops paying through embco owe this wallet."
_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
    ],
    "SupplierSignIn": [
        {"name": "statement", "type": "string"},
        {"name": "wallet", "type": "address"},
        {"name": "nonce", "type": "string"},
        {"name": "expiresAt", "type": "string"},
    ],
}


@dataclass(frozen=True)
class SupplierSession:
    wallet: str
    expires_at: datetime


def supplier_sign_in_message(wallet: str, nonce: str, expires_at: datetime,
                             chain_id: int = ARC_TESTNET_CHAIN_ID) -> dict[str, Any]:
    """The exact message the supplier signs with eth_signTypedData_v4."""
    return {
        "types": _TYPES,
        "primaryType": "SupplierSignIn",
        "domain": {"name": "embco", "version": "1", "chainId": chain_id},
        "message": {"statement": STATEMENT, "wallet": wallet, "nonce": nonce,
                    "expiresAt": expires_at.isoformat()},
    }


class SupplierAuth:
    def __init__(self, *, chain_id: int = ARC_TESTNET_CHAIN_ID,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._chain_id = chain_id
        self._clock = clock
        self._nonces: dict[str, tuple[str, datetime]] = {}
        self._sessions: dict[str, SupplierSession] = {}

    def challenge(self, wallet: str) -> dict[str, Any]:
        self._forget_expired()
        if len(self._nonces) >= MAX_OPEN_REQUESTS:
            raise AuthError("too many sign-in requests; try again in a few minutes")
        nonce = secrets.token_hex(16)
        expires_at = self._clock() + NONCE_LIFETIME
        self._nonces[nonce] = (wallet, expires_at)
        return supplier_sign_in_message(wallet, nonce, expires_at, self._chain_id)

    def sign_in(self, nonce: str, signature: str) -> tuple[str, SupplierSession]:
        """(session token, session). The nonce is spent even when the signature fails."""
        entry = self._nonces.pop(nonce, None)
        if entry is None or entry[1] < self._clock():
            raise AuthError("the sign-in request expired; ask for a new one")
        wallet, expires_at = entry
        message = supplier_sign_in_message(wallet, nonce, expires_at, self._chain_id)
        try:
            signer = Account.recover_message(encode_typed_data(full_message=message),
                                             signature=signature)
        except Exception as error:  # noqa: BLE001 (any malformed signature is a failed sign-in)
            raise AuthError("the signature is not valid") from error
        if signer.lower() != wallet.lower():
            raise AuthError("the signature was not made by this wallet")
        token = secrets.token_urlsafe(32)
        session = SupplierSession(wallet=signer, expires_at=self._clock() + SESSION_LIFETIME)
        self._sessions[token] = session
        return token, session

    def session(self, token: str | None) -> SupplierSession:
        session = self._sessions.get(token or "")
        if session is None or session.expires_at < self._clock():
            raise AuthError("sign in with the wallet you are paid to")
        return session

    def sign_out(self, token: str | None) -> None:
        self._sessions.pop(token or "", None)

    def _forget_expired(self) -> None:
        now = self._clock()
        self._nonces = {n: v for n, v in self._nonces.items() if v[1] >= now}
        self._sessions = {t: s for t, s in self._sessions.items() if s.expires_at >= now}
