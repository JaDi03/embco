import json
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from fastapi.testclient import TestClient

from embco.controls import WalletChallenge
from embco.hub.api import create_app
from embco.hub.auth import AuthError
from embco.hub.chain import ShopLimits
from embco.hub.supplier_auth import SupplierAuth
from embco.ledger.erpnext.client import FrappeClient
from embco.shops import ErpCredentials, ShopConfig, ShopStore, new_key
from embco.shops.inbox import INBOX
from embco.signing import typed_data
from test_hub import AGENT_ON_CONTRACT, OTHER_SHOP, SHOP, auth, clock, owner, sign  # noqa: F401

SUPPLIER = "Central Provisions Ltd"
SUPPLIER_KEY = Account.create()  # the supplier's wallet, fresh on every run
NOW = datetime.now(UTC)  # the hub checks expiry against the real clock


class FakeErp:
    def __init__(self, email="orders@supplier.test"):
        self.email, self.sent = email, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/api/resource/Supplier/{SUPPLIER}":
            return httpx.Response(200, json={"data": {"name": SUPPLIER, "email_id": self.email}})
        if path.endswith("communication.email.make"):
            self.sent.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
            return httpx.Response(200, json={"message": {"name": "COMM-1"}})
        raise AssertionError(f"unexpected {request.method} {path}")


def challenge(expires=NOW + timedelta(days=7)):
    return WalletChallenge(supplier=SUPPLIER, wallet=SUPPLIER_KEY.address, payer="TEST Shop",
                           nonce=secrets.token_hex(16), issued_at=NOW, expires_at=expires)


def view(c=None, other_wallet=None):
    c = c or challenge()
    return {"at": NOW.isoformat(), "company": "TEST Shop", "network": "Arc testnet",
            "suppliers": {
                SUPPLIER: {
                    "wallet": c.wallet,
                    "invoices": [{"invoice": "PINV-5", "amount": "335", "due_date": None,
                                  "status": "SIGNATURE_NEEDED"}],
                    "payments": [], "last_signature": None,
                    "challenge": {"wallet": c.wallet, "expires_at": c.expires_at.isoformat(),
                                  "typed_data": typed_data(c)}},
                "Other Supplier": {
                    "wallet": other_wallet or Account.create().address,
                    "invoices": [{"invoice": "PINV-9", "amount": "50", "due_date": None,
                                  "status": "SCHEDULED"}],
                    "payments": [], "challenge": None, "last_signature": None},
            }}


@pytest.fixture
def hub(tmp_path, auth):  # noqa: F811
    store = ShopStore(tmp_path / "shops", new_key())
    store.save(ShopConfig(shop=SHOP, erp_url="https://shop.frappe.cloud", company="TEST Shop",
                          max_per_payment="300", weekly_budget="2000"),
               ErpCredentials(api_key=secrets.token_hex(8), api_secret=secrets.token_urlsafe(24)))
    store.write_supplier_view(SHOP, view())
    erp = FakeErp()
    app = create_app(
        store=store, auth=auth, units=None,
        limits_of=lambda shop: ShopLimits(Decimal("300"), Decimal("2000"), AGENT_ON_CONTRACT),
        frappe_for=lambda url, key, secret: FrappeClient(
            url, key, secret, client=httpx.Client(transport=httpx.MockTransport(erp))),
        public_url="https://app.example.com",
    )
    return {"app": app, "store": store, "erp": erp}


def client_of(hub):
    return TestClient(hub["app"], base_url="https://testserver")


def owner_client(hub, owner):  # noqa: F811
    client = client_of(hub)
    message = client.post(f"/api/shops/{SHOP}/sign-in-request").json()
    response = client.post(f"/api/shops/{SHOP}/sign-in", json={
        "nonce": message["message"]["nonce"], "signature": sign(owner, message)})
    assert response.status_code == 200, response.text
    return client


def supplier_client(hub, key=SUPPLIER_KEY):
    client = client_of(hub)
    message = client.post("/api/supplier/sign-in-request", json={"wallet": key.address}).json()
    response = client.post("/api/supplier/sign-in", json={
        "nonce": message["message"]["nonce"], "signature": sign(key, message)})
    assert response.status_code == 200, response.text
    return client


def signature_of(typed, key=SUPPLIER_KEY):
    return Account.sign_message(encode_typed_data(full_message=typed), key.key).signature.hex()


# ---- the owner


def test_the_owner_lists_suppliers_and_the_address_to_give_them(hub, owner):  # noqa: F811
    body = owner_client(hub, owner).get(f"/api/shops/{SHOP}/suppliers").json()
    assert body["site"] == "app.example.com/supplier"
    assert [r["supplier"] for r in body["suppliers"]] == [SUPPLIER, "Other Supplier"]
    assert body["suppliers"][0]["signature_needed"]
    assert body["suppliers"][0]["wallet"] == SUPPLIER_KEY.address


def test_owner_routes_need_the_owner(hub):
    client = client_of(hub)
    assert client.get(f"/api/shops/{SHOP}/suppliers").status_code == 401
    assert client.post(f"/api/shops/{SHOP}/suppliers/{SUPPLIER}/email").status_code == 401


def test_the_notice_is_emailed_through_the_shops_erp_without_a_link(hub, owner):  # noqa: F811
    response = owner_client(hub, owner).post(f"/api/shops/{SHOP}/suppliers/{SUPPLIER}/email")
    assert response.status_code == 200, response.text
    [mail] = hub["erp"].sent
    assert mail["recipients"] == "orders@supplier.test" and mail["send_email"] == "1"
    assert mail["doctype"] == "Supplier" and mail["name"] == SUPPLIER
    assert "app.example.com/supplier" in mail["content"]
    assert SUPPLIER_KEY.address in mail["content"]
    assert "href" not in mail["content"] and "https://" not in mail["content"]
    assert "private key" in mail["content"]


def test_a_supplier_without_email_is_reported(hub, owner):  # noqa: F811
    hub["erp"].email = ""
    response = owner_client(hub, owner).post(f"/api/shops/{SHOP}/suppliers/{SUPPLIER}/email")
    assert response.status_code == 502 and "no email" in response.json()["error"]
    assert hub["erp"].sent == []


def test_no_notice_for_a_supplier_the_agent_has_not_seen(hub, owner):  # noqa: F811
    response = owner_client(hub, owner).post(f"/api/shops/{SHOP}/suppliers/Nobody/email")
    assert response.status_code == 404


# ---- the supplier


def test_a_supplier_signs_in_with_its_wallet_and_sees_only_its_part(hub):
    body = supplier_client(hub).get("/api/supplier/me").json()
    assert body["wallet"] == SUPPLIER_KEY.address
    [entry] = body["shops"]
    assert entry["shop"] == SHOP and entry["supplier"] == SUPPLIER
    assert entry["company"] == "TEST Shop"
    assert [i["invoice"] for i in entry["invoices"]] == ["PINV-5"]
    assert "Other Supplier" not in json.dumps(body)


def test_a_wallet_with_nothing_on_file_sees_nothing(hub):
    body = supplier_client(hub, Account.create()).get("/api/supplier/me").json()
    assert body["shops"] == []


def test_the_page_needs_a_signed_in_wallet(hub):
    assert client_of(hub).get("/api/supplier/me").status_code == 401


def test_a_sign_in_for_one_wallet_cannot_be_signed_by_another(hub):
    client = client_of(hub)
    message = client.post("/api/supplier/sign-in-request",
                          json={"wallet": SUPPLIER_KEY.address}).json()
    response = client.post("/api/supplier/sign-in", json={
        "nonce": message["message"]["nonce"], "signature": sign(Account.create(), message)})
    assert response.status_code == 401
    assert client.get("/api/supplier/me").status_code == 401


def test_a_sign_in_request_needs_a_wallet_address(hub):
    response = client_of(hub).post("/api/supplier/sign-in-request", json={"wallet": "abc"})
    assert response.status_code == 422


def test_sign_in_requests_are_capped(monkeypatch):
    monkeypatch.setattr("embco.hub.supplier_auth.MAX_OPEN_REQUESTS", 2)
    supplier_auth = SupplierAuth()
    supplier_auth.challenge(SUPPLIER_KEY.address)
    supplier_auth.challenge(SUPPLIER_KEY.address)
    with pytest.raises(AuthError, match="too many"):
        supplier_auth.challenge(SUPPLIER_KEY.address)


def test_a_right_signature_is_left_for_the_agent(hub):
    client = supplier_client(hub)
    [entry] = client.get("/api/supplier/me").json()["shops"]
    signature = signature_of(entry["challenge"]["typed_data"])
    response = client.post("/api/supplier/signature", json={
        "shop": SHOP, "supplier": SUPPLIER, "signature": signature})
    assert response.status_code == 200, response.text
    [item] = list((hub["store"].folder(SHOP) / INBOX).glob("*.json"))
    assert json.loads(item.read_text()) | {"received_at": None} == {
        "supplier": SUPPLIER, "signature": signature, "received_at": None}


def test_a_signature_from_another_wallet_is_refused_at_once(hub):
    client = supplier_client(hub)
    [entry] = client.get("/api/supplier/me").json()["shops"]
    typed = entry["challenge"]["typed_data"]
    for signature in (signature_of(typed, Account.create()), "0x1234"):
        response = client.post("/api/supplier/signature", json={
            "shop": SHOP, "supplier": SUPPLIER, "signature": signature})
        assert response.status_code == 422
    assert not (hub["store"].folder(SHOP) / INBOX).exists()


def test_a_wallet_cannot_sign_for_another_supplier(hub):
    intruder = Account.create()
    hub["store"].write_supplier_view(SHOP, view(other_wallet=intruder.address))
    response = supplier_client(hub, intruder).post("/api/supplier/signature", json={
        "shop": SHOP, "supplier": SUPPLIER, "signature": "0x1234"})
    assert response.status_code == 404


def test_a_signature_for_another_shop_finds_nothing(hub):
    response = supplier_client(hub).post("/api/supplier/signature", json={
        "shop": OTHER_SHOP, "supplier": SUPPLIER, "signature": "0x1234"})
    assert response.status_code == 404


def test_an_expired_request_cannot_be_signed(hub):
    old = challenge(expires=NOW - timedelta(minutes=1))
    hub["store"].write_supplier_view(SHOP, view(old))
    response = supplier_client(hub).post("/api/supplier/signature", json={
        "shop": SHOP, "supplier": SUPPLIER, "signature": signature_of(typed_data(old))})
    assert response.status_code == 409 and "expired" in response.json()["error"]
