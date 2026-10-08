"""Who may manage a shop's agent: the wallet that owns the shop contract, proven by a signature.

The dashboard asks for a nonce, the owner signs a readable EIP-712 message with it, and the hub
checks on chain that our factory lists the shop under the signer. A nonce works once and for a
few minutes; a session lives in memory, so a restart only asks the owner to sign again.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data

from embco.signing.wallet_ownership import ARC_TESTNET_CHAIN_ID

NONCE_LIFETIME = timedelta(minutes=5)
SESSION_LIFETIME = timedelta(hours=12)
STATEMENT = "Sign in to manage the embco agent of this shop."
_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
    ],
    "SignIn": [
        {"name": "statement", "type": "string"},
        {"name": "shop", "type": "address"},
        {"name": "nonce", "type": "string"},
        {"name": "expiresAt", "type": "string"},
    ],
}

Clock = Callable[[], datetime]


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Session:
    shop: str
    owner: str
    expires_at: datetime


def sign_in_message(shop: str, nonce: str, expires_at: datetime,
                    chain_id: int = ARC_TESTNET_CHAIN_ID) -> dict[str, Any]:
    """The exact message the owner signs with eth_signTypedData_v4."""
    return {
        "types": _TYPES,
        "primaryType": "SignIn",
        "domain": {"name": "embco", "version": "1", "chainId": chain_id},
        "message": {"statement": STATEMENT, "shop": shop, "nonce": nonce,
                    "expiresAt": expires_at.isoformat()},
    }


class Auth:
    def __init__(self, shops_of: Callable[[str], list[str]], *,
                 chain_id: int = ARC_TESTNET_CHAIN_ID,
                 clock: Clock = lambda: datetime.now(UTC)) -> None:
        self._shops_of = shops_of
        self._chain_id = chain_id
        self._clock = clock
        self._nonces: dict[str, tuple[str, datetime]] = {}
        self._sessions: dict[str, Session] = {}

    def challenge(self, shop: str) -> dict[str, Any]:
        self._forget_expired()
        nonce = secrets.token_hex(16)
        expires_at = self._clock() + NONCE_LIFETIME
        self._nonces[nonce] = (shop, expires_at)
        return sign_in_message(shop, nonce, expires_at, self._chain_id)

    def sign_in(self, shop: str, nonce: str, signature: str) -> tuple[str, Session]:
        """(session token, session). The nonce is spent even when the signature fails."""
        entry = self._nonces.pop(nonce, None)
        if entry is None or entry[0] != shop or entry[1] < self._clock():
            raise AuthError("the sign-in request expired; ask for a new one")
        message = sign_in_message(shop, nonce, entry[1], self._chain_id)
        try:
            signer = Account.recover_message(encode_typed_data(full_message=message),
                                             signature=signature)
        except Exception as error:  # noqa: BLE001 (any malformed signature is a failed sign-in)
            raise AuthError("the signature is not valid") from error
        if shop not in {s.lower() for s in self._shops_of(signer)}:
            raise AuthError("this wallet does not own the shop")
        token = secrets.token_urlsafe(32)
        session = Session(shop=shop, owner=signer, expires_at=self._clock() + SESSION_LIFETIME)
        self._sessions[token] = session
        return token, session

    def session(self, token: str | None, shop: str) -> Session:
        session = self._sessions.get(token or "")
        if session is None or session.expires_at < self._clock():
            raise AuthError("sign in with the shop owner's wallet")
        if session.shop != shop:
            raise AuthError("this session is for another shop")
        return session

    def sign_out(self, token: str | None) -> None:
        self._sessions.pop(token or "", None)

    def _forget_expired(self) -> None:
        now = self._clock()
        self._nonces = {n: v for n, v in self._nonces.items() if v[1] >= now}
        self._sessions = {t: s for t, s in self._sessions.items() if s.expires_at >= now}
