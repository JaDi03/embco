import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from fastapi.testclient import TestClient

from embco.circle import CircleWallet
from embco.hub.api import create_app
from embco.hub.auth import Auth, AuthError
from embco.hub.chain import ShopLimits
from embco.hub.guard import UnsafeUrl, check_erp_url
from embco.ledger.erpnext.client import FrappeClient
from embco.shops import ShopStore, new_key

SHOP = "0x" + "a1" * 20
OTHER_SHOP = "0x" + "b2" * 20
AGENT_ON_CONTRACT = "0x" + "00" * 20


def public(host, port):
    return ["104.18.20.1"]


# ---- the ERP address guard


@pytest.mark.parametrize("url, why", [
    ("http://shop.frappe.cloud", "https"),
    ("https://user:pass@shop.frappe.cloud", "credentials"),
    ("https://shop.frappe.cloud/api/resource/User", "site address"),
    ("https://shop.frappe.cloud/?a=1", "query"),
])
def test_an_erp_address_must_be_a_plain_https_site(url, why):
    with pytest.raises(UnsafeUrl, match=why):
        check_erp_url(url, resolve=public)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254",
                                     "::1", "172.17.0.2", "100.64.0.1"])
def test_an_erp_address_never_leads_inside(address):
    with pytest.raises(UnsafeUrl, match="private or local"):
        check_erp_url("https://erp.example.com", resolve=lambda h, p: [address])


def test_one_inside_address_among_public_ones_is_refused():
    with pytest.raises(UnsafeUrl):
        check_erp_url("https://erp.example.com", resolve=lambda h, p: ["104.18.20.1", "10.0.0.5"])


def test_a_pasted_desk_address_becomes_the_site_address():
    assert check_erp_url("https://shop.frappe.cloud/desk/", resolve=public) == \
        "https://shop.frappe.cloud"
    assert check_erp_url("https://erp.example.com:8443", resolve=public) == \
        "https://erp.example.com:8443"


# ---- signing in with the shop owner's wallet


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


def sign(account, message):
    return Account.sign_message(encode_typed_data(full_message=message),
                                account.key).signature.hex()


@pytest.fixture
def owner():
    return Account.create()  # generated at run time: no key literal in the tests


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def auth(owner, clock):
    return Auth(lambda wallet: [SHOP] if wallet == owner.address else [], clock=clock)


def test_the_shop_owner_signs_in(auth, owner):
    message = auth.challenge(SHOP)
    token, session = auth.sign_in(SHOP, message["message"]["nonce"], sign(owner, message))
    assert session.owner == owner.address
    assert auth.session(token, SHOP) == session


def test_another_wallet_cannot_sign_in_for_the_shop(auth):
    message = auth.challenge(SHOP)
    with pytest.raises(AuthError, match="does not own"):
        auth.sign_in(SHOP, message["message"]["nonce"], sign(Account.create(), message))


def test_a_sign_in_request_works_once_and_expires(auth, owner, clock):
    message = auth.challenge(SHOP)
    nonce, signature = message["message"]["nonce"], sign(owner, message)
    auth.sign_in(SHOP, nonce, signature)
    with pytest.raises(AuthError, match="expired"):
        auth.sign_in(SHOP, nonce, signature)
    late = auth.challenge(SHOP)
    clock.now += timedelta(minutes=6)
    with pytest.raises(AuthError, match="expired"):
        auth.sign_in(SHOP, late["message"]["nonce"], sign(owner, late))


def test_a_session_is_for_one_shop_and_ends(auth, owner, clock):
    message = auth.challenge(SHOP)
    token, _ = auth.sign_in(SHOP, message["message"]["nonce"], sign(owner, message))
    with pytest.raises(AuthError, match="another shop"):
        auth.session(token, OTHER_SHOP)
    clock.now += timedelta(hours=13)
    with pytest.raises(AuthError):
        auth.session(token, SHOP)


# ---- the API


def erp_transport(invoices=({"name": "PINV-1"},), status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if status != 200:
            return httpx.Response(status, json={"exc_type": "AuthenticationError"})
        if path.endswith("get_logged_user"):
            return httpx.Response(200, json={"message": "agent@shop.test"})
        if path == "/api/resource/Company":
            return httpx.Response(200, json={"data": [{"name": "TEST Shop"}]})
        if path == "/api/resource/Purchase Invoice":
            return httpx.Response(200, json={"data": list(invoices)})
        if path.endswith("get_payment_entry"):
            return httpx.Response(200, json={"message": {"doctype": "Payment Entry"}})
        if path == "/api/resource/Bank Account":
            return httpx.Response(200, json={"data": [{"name": "Acme USDC - USDC on Arc"}]})
        raise AssertionError(f"unexpected {request.method} {path}")
    return httpx.MockTransport(handler)


class FakeUnits:
    def __init__(self):
        self.started, self.stopped = [], []

    def start(self, shop):
        self.started.append(shop)

    def stop(self, shop):
        self.stopped.append(shop)


class FakeCircle:
    def __init__(self):
        self.created = 0
        self.address = Account.create().address

    def create_wallet_set(self, name, *, idempotency_key=None):
        return "set-1"

    def create_eoa_wallet(self, wallet_set_id, name, *, idempotency_key=None):
        self.created += 1
        return CircleWallet(id="w-1", address=self.address, blockchain="ARC-TESTNET",
                            wallet_set_id=wallet_set_id)


@pytest.fixture
def hub(tmp_path, auth):
    store = ShopStore(tmp_path / "shops", new_key())
    units, circle = FakeUnits(), FakeCircle()
    erp = {"status": 200}
    app = create_app(
        store=store, auth=auth, units=units, circle=circle,
        limits_of=lambda shop: ShopLimits(Decimal("300"), Decimal("2000"), AGENT_ON_CONTRACT),
        frappe_for=lambda url, key, secret: FrappeClient(
            url, key, secret, client=httpx.Client(transport=erp_transport(status=erp["status"]))),
        check_url=lambda url: check_erp_url(url, resolve=public),
    )
    client = TestClient(app, base_url="https://testserver")
    return {"client": client, "store": store, "units": units, "circle": circle, "erp": erp}


def signed_in(hub, owner, shop=SHOP):
    client = hub["client"]
    message = client.post(f"/api/shops/{shop}/sign-in-request").json()
    response = client.post(f"/api/shops/{shop}/sign-in", json={
        "nonce": message["message"]["nonce"], "signature": sign(owner, message)})
    assert response.status_code == 200, response.text
    return client


def connect_body(secret, **extra):
    return {"erp_url": "https://shop.frappe.cloud/desk", "api_key": secrets.token_hex(8),
            "api_secret": secret, "company": "TEST Shop", "wallet_bank": "USDC on Arc", **extra}


def test_nothing_about_a_shop_without_the_owners_signature(hub):
    client = hub["client"]
    assert client.get(f"/api/shops/{SHOP}").status_code == 401
    response = client.post(f"/api/shops/{SHOP}/erp", json=connect_body(secrets.token_hex(16)))
    assert response.status_code == 401
    assert hub["store"].connected() == []


def test_the_owner_connects_the_erp_and_the_shops_agent_starts(hub, owner):
    client = signed_in(hub, owner)
    secret = secrets.token_urlsafe(24)
    response = client.post(f"/api/shops/{SHOP}/erp", json=connect_body(secret))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["probe"]["ok"] and body["agent_wallet"] == hub["circle"].address
    assert body["set_agent_needed"] is True  # the contract does not have this agent yet
    assert secret not in response.text
    assert hub["units"].started == [SHOP]
    config = hub["store"].config(SHOP)
    assert config.erp_url == "https://shop.frappe.cloud"
    assert (config.max_per_payment, config.weekly_budget) == ("300", "2000")  # from the contract
    assert hub["store"].credentials(SHOP).api_secret == secret
    status = client.get(f"/api/shops/{SHOP}")
    assert status.json()["connected"] and secret not in status.text


def test_reconnecting_keeps_the_shops_agent_wallet(hub, owner):
    client = signed_in(hub, owner)
    client.post(f"/api/shops/{SHOP}/erp", json=connect_body(secrets.token_hex(16)))
    client.post(f"/api/shops/{SHOP}/erp", json=connect_body(secrets.token_hex(16)))
    assert hub["circle"].created == 1


def test_keys_that_do_not_work_are_not_saved(hub, owner):
    client = signed_in(hub, owner)
    hub["erp"]["status"] = 401
    response = client.post(f"/api/shops/{SHOP}/erp", json=connect_body(secrets.token_hex(16)))
    assert response.status_code == 422
    assert response.json()["probe"]["ok"] is False
    assert hub["store"].connected() == [] and hub["units"].started == []


def test_an_inside_erp_address_is_refused(hub, owner):
    client = signed_in(hub, owner)
    body =connect_body(secrets.token_hex(16), erp_url="http://shop.frappe.cloud")
    assert client.post(f"/api/shops/{SHOP}/erp", json=body).status_code == 422


def test_a_bad_request_never_echoes_the_secret(hub, owner):
    client = signed_in(hub, owner)
    secret = secrets.token_urlsafe(24)
    body = connect_body(secret, payment_extra={"paid_amount": "1"})
    response = client.post(f"/api/shops/{SHOP}/erp", json=body)
    assert response.status_code == 422
    assert secret not in response.text and "payment_extra" in response.text


def test_disconnecting_stops_the_agent_and_forgets_the_keys(hub, owner):
    client = signed_in(hub, owner)
    client.post(f"/api/shops/{SHOP}/erp", json=connect_body(secrets.token_hex(16)))
    assert client.delete(f"/api/shops/{SHOP}/erp").json() == {"connected": False}
    assert hub["units"].stopped == [SHOP] and hub["store"].connected() == []


def test_a_session_for_one_shop_cannot_touch_another(hub, owner):
    client = signed_in(hub, owner)
    response = client.post(f"/api/shops/{OTHER_SHOP}/erp", json=connect_body(secrets.token_hex(8)))
    assert response.status_code == 401


@pytest.mark.parametrize("units, text", [(300_000_000, "300"), (2_000_000_000, "2000"),
                                         (1_500_000, "1.5"), (1, "0.000001"), (0, "0")])
def test_contract_amounts_are_read_exactly_and_written_plainly(units, text):
    from embco.hub.chain import _usdc

    assert str(_usdc(units)) == text
