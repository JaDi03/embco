"""Who may manage a shop's agent: the wallet that owns the shop contract, proven by a signature.

The owner signs in with Ethereum (EIP-4361): the hub issues the sign-in message with a nonce, the
owner's wallet signs it, and the hub checks it with the siwe library, then checks on chain that our
factory lists the shop under the signer. A nonce works once and for a few minutes; a session lives
in memory, so a restart only asks the owner to sign again.

The dashboard signs in for ALL_SHOPS instead: one signature covers every shop the wallet owns,
including one it creates later in the same session, and lets the hub make the agent wallets of a
new shop only for the wallet that asked for them.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from services.hub import session_file
from services.hub.siwe_sign_in import VerificationError, generate_nonce, sign_in_text, signer_of
from services.signing.wallet_ownership import ARC_TESTNET_CHAIN_ID

NONCE_LIFETIME = timedelta(minutes=5)
SESSION_LIFETIME = timedelta(hours=12)
ALL_SHOPS = "all-shops"  # a sign-in for every shop of the wallet, and for creating one

Clock = Callable[[], datetime]


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Session:
    shop: str
    owner: str
    expires_at: datetime


def statement(shop: str) -> str:
    if shop == ALL_SHOPS:
        return "Sign in to create and manage your embco shops."
    return f"Sign in to manage the embco agent of the shop contract {shop}."


class Auth:
    def __init__(self, shops_of: Callable[[str], list[str]], *,
                 public_url: str,
                 chain_id: int = ARC_TESTNET_CHAIN_ID,
                 clock: Clock = lambda: datetime.now(UTC),
                 sessions_file: Path | None = None) -> None:
        self._shops_of = shops_of
        self._file = sessions_file
        self._public_url = public_url
        self._chain_id = chain_id
        self._clock = clock
        self._nonces: dict[str, tuple[str, str, datetime]] = {}  # nonce: (shop, message, expiry)
        self._sessions: dict[str, Session] = self._read()  # token hash: session
        self._owned: dict[str, set[str]] = {}  # owner: shops seen on chain, read again on a miss

    def challenge(self, shop: str, address: str) -> dict[str, Any]:
        """The sign-in message for `address` (checksummed) to sign with personal_sign."""
        self._forget_expired()
        nonce = generate_nonce()
        now = self._clock()
        text = sign_in_text(public_url=self._public_url, address=address, statement=statement(shop),
                            nonce=nonce, chain_id=self._chain_id, issued_at=now,
                            expires_at=now + NONCE_LIFETIME)
        self._nonces[nonce] = (shop, text, now + NONCE_LIFETIME)
        return {"message": text, "nonce": nonce}

    def sign_in(self, shop: str, nonce: str, signature: str) -> tuple[str, Session]:
        """(session token, session). The nonce is spent even when the signature fails."""
        entry = self._nonces.pop(nonce, None)
        if entry is None or entry[0] != shop or entry[2] < self._clock():
            raise AuthError("the sign-in request expired; ask for a new one")
        try:
            signer = signer_of(entry[1], signature, public_url=self._public_url, nonce=nonce,
                               chain_id=self._chain_id, now=self._clock())
        except VerificationError as error:
            raise AuthError("the signature is not valid") from error
        if shop != ALL_SHOPS and shop not in {s.lower() for s in self._shops_of(signer)}:
            raise AuthError("this wallet does not own the shop")
        token = secrets.token_urlsafe(32)
        session = Session(shop=shop, owner=signer, expires_at=self._clock() + SESSION_LIFETIME)
        self._sessions[session_file.token_key(token)] = session
        self._write()
        return token, session

    def session(self, token: str | None, shop: str) -> Session:
        session = self._sessions.get(session_file.token_key(token or ""))
        if session is None or session.expires_at < self._clock():
            raise AuthError("sign in with the shop owner's wallet")
        if session.shop == shop:
            return session
        if session.shop != ALL_SHOPS:
            raise AuthError("this session is for another shop")
        if shop not in self._owned.get(session.owner, set()):
            self._owned[session.owner] = {s.lower() for s in self._shops_of(session.owner)}
            if shop not in self._owned[session.owner]:
                raise AuthError("this wallet does not own the shop")
        return session

    def sign_out(self, token: str | None) -> None:
        if self._sessions.pop(session_file.token_key(token or ""), None):
            self._write()

    def _forget_expired(self) -> None:
        now = self._clock()
        self._nonces = {n: v for n, v in self._nonces.items() if v[2] >= now}
        kept = {t: s for t, s in self._sessions.items() if s.expires_at >= now}
        if len(kept) != len(self._sessions):
            self._sessions = kept
            self._write()

    def _read(self) -> dict[str, Session]:
        if self._file is None:
            return {}
        now = self._clock()
        sessions = {}
        for key, e in session_file.load(self._file).items():
            try:
                session = Session(shop=e["shop"], owner=e["owner"],
                                  expires_at=datetime.fromisoformat(e["expires_at"]))
            except (KeyError, TypeError, ValueError):
                continue
            if session.expires_at >= now:
                sessions[key] = session
        return sessions

    def _write(self) -> None:
        if self._file is not None:
            session_file.save(self._file, {
                key: {"shop": s.shop, "owner": s.owner, "expires_at": s.expires_at.isoformat()}
                for key, s in self._sessions.items()})
