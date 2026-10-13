"""How a supplier signs in to its page: with the wallet it is paid to, proven by a signature.

There is no link and no password. The supplier signs in with Ethereum (EIP-4361), checked with
the siwe library. Any wallet can sign in, and it sees only the entries whose wallet on file in a
shop's ERP is that wallet. A nonce works once and for a few minutes; a session lives in memory, so
a restart only asks the supplier to sign again.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from services.hub.auth import NONCE_LIFETIME, SESSION_LIFETIME, AuthError
from services.hub.siwe_sign_in import VerificationError, generate_nonce, sign_in_text, signer_of
from services.signing.wallet_ownership import ARC_TESTNET_CHAIN_ID

MAX_OPEN_REQUESTS = 10_000  # sign-in requests are unauthenticated: cap what they can hold
STATEMENT = "Sign in to see what shops paying through embco owe this wallet."


@dataclass(frozen=True)
class SupplierSession:
    wallet: str
    expires_at: datetime


class SupplierAuth:
    def __init__(self, *, public_url: str, chain_id: int = ARC_TESTNET_CHAIN_ID,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._public_url = public_url
        self._chain_id = chain_id
        self._clock = clock
        self._nonces: dict[str, tuple[str, str, datetime]] = {}  # nonce: (wallet, message, expiry)
        self._sessions: dict[str, SupplierSession] = {}

    def challenge(self, wallet: str) -> dict[str, Any]:
        """The sign-in message for `wallet` (checksummed) to sign with personal_sign."""
        self._forget_expired()
        if len(self._nonces) >= MAX_OPEN_REQUESTS:
            raise AuthError("too many sign-in requests; try again in a few minutes")
        nonce = generate_nonce()
        now = self._clock()
        text = sign_in_text(public_url=self._public_url, address=wallet, statement=STATEMENT,
                            nonce=nonce, chain_id=self._chain_id, issued_at=now,
                            expires_at=now + NONCE_LIFETIME)
        self._nonces[nonce] = (wallet, text, now + NONCE_LIFETIME)
        return {"message": text, "nonce": nonce}

    def sign_in(self, nonce: str, signature: str) -> tuple[str, SupplierSession]:
        """(session token, session). The nonce is spent even when the signature fails."""
        entry = self._nonces.pop(nonce, None)
        if entry is None or entry[2] < self._clock():
            raise AuthError("the sign-in request expired; ask for a new one")
        wallet, text, _expires_at = entry
        try:
            signer = signer_of(text, signature, public_url=self._public_url, nonce=nonce,
                               chain_id=self._chain_id, now=self._clock())
        except VerificationError as error:
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
        self._nonces = {n: v for n, v in self._nonces.items() if v[2] >= now}
        self._sessions = {t: s for t, s in self._sessions.items() if s.expires_at >= now}
