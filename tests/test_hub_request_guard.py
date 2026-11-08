import pytest
from fastapi.testclient import TestClient

from services.hub.api import create_app
from services.hub.auth import Auth
from services.hub.request_guard import MAX_BODY, OTHER_ORIGIN, TOO_LARGE
from services.shops import ShopStore, new_key

SHOP = "0x" + "ab" * 20
SIGN_IN = "/api/owners/sign-in-request"


class NoUnits:
    def start(self, shop):
        raise AssertionError("no agent starts in these tests")

    stop = ensure = start


@pytest.fixture
def client(tmp_path):
    app = create_app(store=ShopStore(tmp_path, new_key()),
                     auth=Auth(lambda wallet: [], public_url="https://app.embco.xyz"),
                     limits_of=lambda shop: None, units=NoUnits(),
                     rpc_url="https://node.invalid/rpc")
    return TestClient(app)


def chunks(size):
    """A body sent without Content-Length, piece by piece."""
    for _ in range(size // 1024):
        yield b"x" * 1024


@pytest.mark.parametrize("path", [SIGN_IN, "/rpc"])
def test_a_declared_body_above_the_limit_gets_413(client, path):
    answer = client.post(path, content=b"x" * (MAX_BODY + 1),
                         headers={"Content-Type": "application/json"})
    assert answer.status_code == 413
    assert answer.json() == {"error": TOO_LARGE}


@pytest.mark.parametrize("path", [SIGN_IN, "/rpc"])
def test_a_body_without_length_stops_at_the_limit(client, path):
    answer = client.post(path, content=chunks(MAX_BODY + 4096),
                         headers={"Content-Type": "application/json"})
    assert "content-length" not in {k.lower() for k in answer.request.headers}
    assert answer.status_code == 413
    assert answer.json() == {"error": TOO_LARGE}


def test_a_body_within_the_limit_reaches_the_route(client):
    answer = client.post(SIGN_IN, json={"address": "not a wallet"})
    assert answer.status_code == 422  # the route's own answer


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "same-site"},  # the ERP or another site under the same domain
    {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "https://erp.embco.xyz"},
    {"Origin": "null"},
])
def test_a_change_from_another_origin_is_refused(client, headers):
    for method, path in [("POST", f"/api/shops/{SHOP}/check"),
                         ("POST", f"/api/shops/{SHOP}/agent-balance/return"),
                         ("DELETE", f"/api/shops/{SHOP}/erp"),
                         ("POST", SIGN_IN)]:
        answer = client.request(method, path, headers=headers)
        assert answer.status_code == 403, (method, path)
        assert answer.json() == {"error": OTHER_ORIGIN}


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "same-origin"},
    {"Origin": "https://app.embco.xyz"},
    {"Sec-Fetch-Site": "same-origin", "Origin": "https://app.embco.xyz"},
    {},  # not a browser: it holds no browser's cookie
])
def test_a_change_from_the_dashboard_goes_through(client, headers):
    answer = client.post(f"/api/shops/{SHOP}/check", headers=headers)
    assert answer.status_code == 401  # reached the route, which asks for a session


def test_reading_from_another_origin_is_left_to_the_session(client):
    answer = client.get(f"/api/shops/{SHOP}", headers={"Sec-Fetch-Site": "cross-site"})
    assert answer.status_code == 401


def test_the_wallet_reaches_the_node_door_from_its_own_origin(client):
    answer = client.post("/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "debug_traceCall"},
                         headers={"Origin": "chrome-extension://wallet"})
    assert answer.status_code == 200
    assert answer.json()["error"]["message"] == "method not available here"


@pytest.mark.parametrize("method, path, headers", [
    ("GET", f"/api/shops/{SHOP}", {}),
    ("POST", SIGN_IN, {"Origin": "https://erp.embco.xyz"}),
    ("POST", SIGN_IN, {}),
])
def test_every_api_answer_is_kept_by_nobody(client, method, path, headers):
    answer = client.request(method, path, headers=headers, json={"address": "x"})
    assert answer.headers["cache-control"] == "no-store"
