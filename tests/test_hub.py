import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from services.circle import CircleWallet
from services.erp.erpnext.client import FrappeClient
from services.hub.api import create_app
from services.hub.auth import Auth, AuthError
from services.hub.chain import ShopLimits
from services.hub.guard import UnsafeUrl, check_erp_url
from services.hub.units import UnitError, start_connected
from services.shops import ErpCredentials, ShopConfig, ShopStore, new_key

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
    """Signs a sign-in request's EIP-4361 text with personal_sign, as the wallet does."""
    return Account.sign_message(encode_defunct(text=message["message"]),
                                account.key).signature.hex()


@pytest.fixture
def owner():
    return Account.create()  # generated at run time: no key literal in the tests


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def auth(owner, clock):
    return Auth(lambda wallet: [SHOP] if wallet == owner.address else [],
                public_url="https://app.embco.xyz", clock=clock)


def test_the_shop_owner_signs_in(auth, owner):
    message = auth.challenge(SHOP, owner.address)
    assert message["message"].startswith("app.embco.xyz wants you to sign in with your Ethereum")
    token, session = auth.sign_in(SHOP, message["nonce"], sign(owner, message))
    assert session.owner == owner.address
    assert auth.session(token, SHOP) == session


def test_another_wallet_cannot_sign_in_for_the_shop(auth, owner):
    stranger = Account.create()
    message = auth.challenge(SHOP, stranger.address)
    with pytest.raises(AuthError, match="does not own"):
        auth.sign_in(SHOP, message["nonce"], sign(stranger, message))
    message = auth.challenge(SHOP, owner.address)  # the owner's message, signed by someone else
    with pytest.raises(AuthError, match="not valid"):
        auth.sign_in(SHOP, message["nonce"], sign(stranger, message))


def test_a_sign_in_request_works_once_and_expires(auth, owner, clock):
    message = auth.challenge(SHOP, owner.address)
    nonce, signature = message["nonce"], sign(owner, message)
    auth.sign_in(SHOP, nonce, signature)
    with pytest.raises(AuthError, match="expired"):
        auth.sign_in(SHOP, nonce, signature)
    late = auth.challenge(SHOP, owner.address)
    clock.now += timedelta(minutes=6)
    with pytest.raises(AuthError, match="expired"):
        auth.sign_in(SHOP, late["nonce"], sign(owner, late))


def test_a_session_is_for_one_shop_and_ends(auth, owner, clock):
    message = auth.challenge(SHOP, owner.address)
    token, _ = auth.sign_in(SHOP, message["nonce"], sign(owner, message))
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
    def __init__(self, failing=()):
        self.started, self.stopped, self.ensured = [], [], []
        self.failing = set(failing)

    def start(self, shop):
        self.started.append(shop)

    def ensure(self, shop):
        if shop in self.failing:
            raise UnitError(f"systemctl start embco-shop@{shop}.service failed (1)")
        self.ensured.append(shop)

    def stop(self, shop):
        self.stopped.append(shop)


class FakeCircle:
    """Circle as the hub sees it: wallets by idempotency key, so asking again returns the same."""

    def __init__(self):
        self.created = 0
        self.address = Account.create().address
        self.wallets: dict[str, CircleWallet] = {}

    def create_wallet_set(self, name, *, idempotency_key=None):
        return "set-" + str(idempotency_key)[:8]

    def create_eoa_wallet(self, wallet_set_id, name, *, idempotency_key=None):
        self.created += 1
        return CircleWallet(id="w-1", address=self.address, blockchain="ARC-TESTNET",
                            wallet_set_id=wallet_set_id)

    def create_wallet(self, wallet_set_id, name, *, account_type, idempotency_key=None):
        if idempotency_key not in self.wallets:
            self.wallets[idempotency_key] = CircleWallet(
                id=f"w-{len(self.wallets) + 2}", address=Account.create().address,
                blockchain="ARC-TESTNET", wallet_set_id=wallet_set_id, account_type=account_type)
        return self.wallets[idempotency_key]

    def find_wallet(self, address, blockchain):
        return next((w for w in self.wallets.values() if w.address == address), None)

    def wallets_in_set(self, wallet_set_id, blockchain="ARC-TESTNET"):
        return [w for w in self.wallets.values() if w.wallet_set_id == wallet_set_id]


@pytest.fixture
def hub(tmp_path, auth, owner):
    store = ShopStore(tmp_path / "shops", new_key())
    units, circle = FakeUnits(), FakeCircle()
    erp = {"status": 200}
    chain = {"agent": AGENT_ON_CONTRACT, "shops": []}
    app = create_app(
        store=store, auth=auth, units=units, circle=circle,
        shops_of=lambda wallet: list(chain["shops"]) if wallet == owner.address else [],
        limits_of=lambda shop: ShopLimits(Decimal("300"), Decimal("2000"), chain["agent"]),
        frappe_for=lambda url, key, secret: FrappeClient(
            url, key, secret, client=httpx.Client(transport=erp_transport(status=erp["status"]))),
        check_url=lambda url: check_erp_url(url, resolve=public),
    )
    client = TestClient(app, base_url="https://testserver")
    return {"client": client, "store": store, "units": units, "circle": circle, "erp": erp,
            "chain": chain}


def signed_in(hub, owner, shop=SHOP):
    client = hub["client"]
    message = client.post(f"/api/shops/{shop}/sign-in-request",
                          json={"address": owner.address}).json()
    response = client.post(f"/api/shops/{shop}/sign-in", json={
        "nonce": message["nonce"], "signature": sign(owner, message)})
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
    from services.hub.chain import _usdc

    assert str(_usdc(units)) == text


def test_circle_names_stay_short_and_keys_stay_the_same_for_a_shop():
    from services.hub.wallets import create_agent_wallet, wallet_names

    assert all(len(name) <= 30 for name in wallet_names(SHOP))  # Circle refused 53 characters
    calls = []

    class Recording(FakeCircle):
        def create_wallet_set(self, name, *, idempotency_key=None):
            calls.append((name, idempotency_key))
            return "set-1"

        def create_eoa_wallet(self, wallet_set_id, name, *, idempotency_key=None):
            calls.append((name, idempotency_key))
            return super().create_eoa_wallet(wallet_set_id, name)

    create_agent_wallet(Recording(), SHOP)
    create_agent_wallet(Recording(), SHOP)
    assert calls[:2] == calls[2:]  # a retry sends the same names and idempotency keys
    assert calls[0] == ("embco " + SHOP[:10], calls[0][1])


# ---- a new shop is created with its agent


def signed_in_to_create(hub, account):
    client = hub["client"]
    message = client.post("/api/owners/sign-in-request", json={"address": account.address}).json()
    assert "create and manage your embco shops" in message["message"]
    response = client.post("/api/owners/sign-in", json={
        "nonce": message["nonce"], "signature": sign(account, message)})
    assert response.status_code == 200, response.text
    return client


def test_no_agent_wallets_without_the_owners_signature(hub):
    assert hub["client"].post("/api/owners/agent-wallets").status_code == 401
    assert hub["circle"].wallets == {}


def test_a_new_shop_gets_a_gasless_agent_and_a_signer_before_its_contract(hub, owner):
    client = signed_in_to_create(hub, owner)
    first = client.post("/api/owners/agent-wallets").json()
    assert client.post("/api/owners/agent-wallets").json() == first  # asking again: same two
    kinds = {w.address: w.account_type for w in hub["circle"].wallets.values()}
    assert kinds == {first["agent"]: "SCA", first["signer"]: "EOA"}
    hub["chain"]["shops"].append(SHOP)  # the owner created the shop: the next one is new
    second = client.post("/api/owners/agent-wallets").json()
    assert {second["agent"], second["signer"]}.isdisjoint({first["agent"], first["signer"]})


def test_one_signature_covers_the_owners_shops_only(hub, owner):
    client = signed_in_to_create(hub, owner)
    assert client.get(f"/api/shops/{SHOP}").status_code == 200
    assert client.get(f"/api/shops/{OTHER_SHOP}").status_code == 401


def test_a_shop_created_after_signing_in_needs_no_second_signature(owner, clock):
    from services.hub.auth import ALL_SHOPS

    owned, reads = [], []
    auth = Auth(lambda wallet: reads.append(wallet) or list(owned),
                public_url="https://app.embco.xyz", clock=clock)
    message = auth.challenge(ALL_SHOPS, owner.address)
    token, _ = auth.sign_in(ALL_SHOPS, message["nonce"], sign(owner, message))
    with pytest.raises(AuthError, match="does not own"):
        auth.session(token, SHOP)
    owned.append(SHOP)  # the owner creates the shop
    assert auth.session(token, SHOP).owner == owner.address
    auth.session(token, SHOP)
    assert len(reads) == 2  # read again only on a miss


def test_a_shop_created_with_its_agent_needs_no_erp_to_show_it(hub, owner):
    client = signed_in_to_create(hub, owner)
    wallets = client.post("/api/owners/agent-wallets").json()
    hub["chain"].update(agent=wallets["agent"], shops=[SHOP])
    assert client.get(f"/api/shops/{SHOP}/agent").json() == {
        "agent": wallets["agent"], "ours": True, "signer": wallets["signer"], "gasless": True}
    body = client.post(f"/api/shops/{SHOP}/erp", json=connect_body(secrets.token_hex(16))).json()
    assert body["agent_wallet"] == wallets["agent"] and body["set_agent_needed"] is False
    config = hub["store"].config(SHOP)
    assert config.signer_wallet_address == wallets["signer"]
    assert hub["circle"].created == 0  # no older single wallet was made


def test_an_agent_that_is_not_ours_is_shown_as_such(hub, owner):
    client = signed_in(hub, owner)
    assert client.get(f"/api/shops/{SHOP}/agent").json() == {
        "agent": AGENT_ON_CONTRACT, "ours": False, "signer": None, "gasless": False}


def test_new_shop_wallet_names_stay_short():
    from services.hub.wallets import create_agent_wallets

    names = []

    class Recording(FakeCircle):
        def create_wallet_set(self, name, *, idempotency_key=None):
            names.append(name)
            return super().create_wallet_set(name, idempotency_key=idempotency_key)

        def create_wallet(self, wallet_set_id, name, *, account_type, idempotency_key=None):
            names.append(name)
            return super().create_wallet(wallet_set_id, name, account_type=account_type,
                                         idempotency_key=idempotency_key)

    create_agent_wallets(Recording(), "0x" + "ab" * 20, 11)
    assert names[0] == "embco 0xabababab #12"
    assert all(len(name) <= 30 for name in names)  # Circle refused 53 characters


# ---- after a reboot


def connected_store(tmp_path, *shops):
    store = ShopStore(tmp_path / "shops", new_key())
    for shop in shops:
        keys = ErpCredentials(api_key=secrets.token_hex(8), api_secret=secrets.token_urlsafe(24))
        store.save(ShopConfig(shop=shop, erp_url="https://shop.frappe.cloud", company="TEST Shop",
                              max_per_payment="300", weekly_budget="2000"), keys)
    return store


def test_the_hub_starts_again_the_agents_their_owners_left_on(tmp_path):
    store = connected_store(tmp_path, SHOP, OTHER_SHOP)
    store.set_agent_on(OTHER_SHOP, False, "2026-10-12T00:00:00+00:00")
    (tmp_path / "shops" / ("0x" + "c3" * 20)).mkdir()  # a folder without keys is not connected
    units = FakeUnits()
    assert start_connected(store, units) == [SHOP]
    assert units.ensured == [SHOP] and units.started == []


def test_one_agent_that_fails_to_start_does_not_keep_the_others_off(tmp_path):
    store = connected_store(tmp_path, SHOP, OTHER_SHOP)
    units = FakeUnits(failing={SHOP})
    assert start_connected(store, units) == [OTHER_SHOP]
