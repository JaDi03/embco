"""An owner's sign-in survives a restart of the hub, and the file never holds a usable token."""

import os
from datetime import timedelta

import pytest

from services.hub.auth import SESSION_LIFETIME, Auth, AuthError
from test_hub import SHOP, Clock, sign


def make(owner, clock, path):
    return Auth(lambda wallet: [SHOP] if wallet == owner.address else [],
                public_url="https://app.embco.xyz", clock=clock, sessions_file=path)


@pytest.fixture
def owner():
    from eth_account import Account
    return Account.create()


def signed_in(auth, owner):
    message = auth.challenge(SHOP, owner.address)
    return auth.sign_in(SHOP, message["nonce"], sign(owner, message))


def test_a_restart_keeps_the_owner_signed_in(owner, tmp_path):
    clock, path = Clock(), tmp_path / "owner_sessions.json"
    token, session = signed_in(make(owner, clock, path), owner)
    restarted = make(owner, clock, path)  # a new process reads the file
    assert restarted.session(token, SHOP) == session


def test_the_file_holds_no_token_and_only_the_hub_reads_it(owner, tmp_path):
    clock, path = Clock(), tmp_path / "owner_sessions.json"
    token, _ = signed_in(make(owner, clock, path), owner)
    assert token not in path.read_text(encoding="utf-8")
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_sign_out_and_expiry_hold_after_a_restart(owner, tmp_path):
    clock, path = Clock(), tmp_path / "owner_sessions.json"
    auth = make(owner, clock, path)
    out, _ = signed_in(auth, owner)
    kept, _ = signed_in(auth, owner)
    auth.sign_out(out)
    with pytest.raises(AuthError):
        make(owner, clock, path).session(out, SHOP)
    clock.now += SESSION_LIFETIME + timedelta(seconds=1)
    with pytest.raises(AuthError):
        make(owner, clock, path).session(kept, SHOP)
