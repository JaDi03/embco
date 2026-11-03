"""A shop runs on Arc testnet or Arc mainnet: its keys, node, contracts and ERP mode follow."""

import json
import secrets
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from services.circle import CircleWallet
from services.hub.api import create_app
from services.hub.auth import Auth
from services.hub.chain import ShopChains, ShopLimits
from services.network import MAINNET, TESTNET, network
from services.payments.chain import ArcRpc, ChainError
from services.settings import Settings, SettingsError
from services.shops import ErpCredentials, ShopConfig, ShopStore, new_key
from services.telegram.bot import contract_pauser

SHOP = "0x" + "a1" * 20
OTHER = "0x" + "b2" * 20


def creds():
    return ErpCredentials(api_key=secrets.token_hex(8), api_secret=secrets.token_urlsafe(24))


def config(**kwargs):
    return ShopConfig(shop=SHOP, erp_url="https://erp.example.com", company="TEST Shop",
                      max_per_payment="300", weekly_budget="2000", agent_wallet_id="wallet-a",
                      **kwargs)


def both_platforms():
    """Every network's keys, generated at run time: no key literal in the tests."""
    return {"CIRCLE_API_KEY": secrets.token_hex(8), "CIRCLE_ENTITY_SECRET": secrets.token_hex(8),
            "ARC_TESTNET_RPC_URL": "https://testnet.example.com",
            "CIRCLE_MAINNET_API_KEY": secrets.token_hex(8),
            "CIRCLE_MAINNET_ENTITY_SECRET": secrets.token_hex(8),
            "ARC_MAINNET_RPC_URL": "https://mainnet.example.com"}


@pytest.fixture
def store(tmp_path):
    return ShopStore(tmp_path / "shops", new_key())


# ---- the networks


def test_the_two_networks_are_arc_as_its_docs_and_our_deploys_say():
    assert (TESTNET.chain_id, TESTNET.circle_blockchain) == (5042002, "ARC-TESTNET")
    assert (MAINNET.chain_id, MAINNET.circle_blockchain) == (5042, "ARC")
    assert MAINNET.explorer == "https://explorer.arc.io"
    assert MAINNET.factory == "0x1304d82964c44E2a8cca4D5bc27E30C83A123a50"
    assert MAINNET.first_block == 29369377
    assert network(None) is TESTNET and network("mainnet") is MAINNET
    with pytest.raises(ValueError, match="testnet, mainnet"):
        network("sepolia")


# ---- a shop's settings


def test_a_shop_saved_before_mainnet_stays_on_testnet(store):
    store.save(config(), creds())
    saved = json.loads((store.folder(SHOP) / "config.json").read_text(encoding="utf-8"))
    del saved["network"]  # a config.json written before this field existed
    (store.folder(SHOP) / "config.json").write_text(json.dumps(saved), encoding="utf-8")
    shared = both_platforms()
    settings = store.settings(SHOP, shared)
    assert settings.network is TESTNET and settings.pay and settings.erpnext_draft_payments
    assert settings.circle_api_key == shared["CIRCLE_API_KEY"]
    assert settings.arc_rpc_url == shared["ARC_TESTNET_RPC_URL"]


def test_a_mainnet_shop_uses_only_mainnet_keys_and_submits_every_payment(store):
    store.save(config(network="mainnet"), creds())
    shared = both_platforms()
    settings = store.settings(SHOP, shared)
    assert settings.network is MAINNET and settings.pay
    assert settings.circle_api_key == shared["CIRCLE_MAINNET_API_KEY"]
    assert settings.circle_entity_secret == shared["CIRCLE_MAINNET_ENTITY_SECRET"]
    assert settings.arc_rpc_url == shared["ARC_MAINNET_RPC_URL"]
    assert not settings.erpnext_draft_payments  # real USDC settles the invoice
    assert not store.draft_payments(SHOP)


def test_a_mainnet_shop_does_not_pay_with_testnet_keys(store):
    store.save(config(network="mainnet"), creds())
    testnet_only = {k: v for k, v in both_platforms().items() if "MAINNET" not in k}
    assert not store.settings(SHOP, testnet_only).pay


def test_an_unknown_network_is_refused():
    env = {"EMBCO_ERPNEXT_URL": "https://erp.example.com", "EMBCO_ERPNEXT_API_KEY": "k",
           "EMBCO_ERPNEXT_API_SECRET": secrets.token_hex(8), "EMBCO_COMPANY": "TEST Shop",
           "EMBCO_MAX_PER_PAYMENT": "300", "EMBCO_WEEKLY_BUDGET": "2000",
           "EMBCO_NETWORK": "sepolia"}
    with pytest.raises(SettingsError, match="EMBCO_NETWORK must be testnet or mainnet"):
        Settings.from_env(env)


# ---- the node


def node(answers):
    """An Arc node that answers each method from `answers` and records the calls."""
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        answer = answers[body["method"]]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                         "result": answer(body) if callable(answer) else answer})

    return httpx.Client(transport=httpx.MockTransport(handle)), calls


def test_on_mainnet_the_node_reads_events_from_the_mainnet_factory_block():
    client, calls = node({"eth_blockNumber": hex(MAINNET.first_block + 10), "eth_getLogs": [],
                          "eth_getBlockByNumber": {"timestamp": hex(1)}})
    rpc = ArcRpc("https://mainnet.example.com", client=client, first_block=MAINNET.first_block)
    assert rpc.payment_tx(SHOP, b"\x01" * 32, OTHER, datetime(2026, 11, 1, tzinfo=UTC)) is None
    [logs] = [c for c in calls if c["method"] == "eth_getLogs"]
    assert int(logs["params"][0]["fromBlock"], 16) == MAINNET.first_block  # never testnet's


def test_a_shop_is_on_the_network_where_its_contract_is():
    testnet_client, _ = node({"eth_getCode": "0x"})
    mainnet_client, _ = node({"eth_getCode": lambda b: "0x6080" if b["params"][0].lower() == SHOP
                              else "0x"})
    chains = ShopChains({
        "testnet": (TESTNET, None, ArcRpc("https://t.example.com", client=testnet_client)),
        "mainnet": (MAINNET, None, ArcRpc("https://m.example.com", client=mainnet_client)),
    }, lambda shop: None)
    assert chains.network_of(SHOP) is MAINNET
    assert chains.network_of(OTHER) is TESTNET


def test_a_saved_network_wins_and_one_without_a_node_is_refused():
    client, calls = node({"eth_getCode": "0x6080"})
    rpc = ArcRpc("https://t.example.com", client=client)
    chains = ShopChains({"testnet": (TESTNET, None, rpc)},
                        lambda shop: "testnet" if shop == SHOP else "mainnet")
    assert chains.network_of(SHOP) is TESTNET and calls == []
    with pytest.raises(ChainError, match="no node for the shop's network"):
        chains.network_of(OTHER)


# ---- the hub


class FakeCircle:
    def __init__(self):
        self.blockchains = []
        self.executed = []

    def create_wallet_set(self, name, *, idempotency_key=None):
        return "set-1"

    def create_wallet(self, wallet_set_id, name, *, account_type, blockchain="ARC-TESTNET",
                      idempotency_key=None):
        self.blockchains.append(blockchain)
        return CircleWallet(id=f"w-{len(self.blockchains)}", address=Account.create().address,
                            blockchain=blockchain, wallet_set_id=wallet_set_id,
                            account_type=account_type)

    def execute_contract(self, wallet_id, to, data, *, idempotency_key, ref_id):
        self.executed.append((wallet_id, to))
        return type("Tx", (), {"id": "tx-1"})()


class Units:
    def start(self, shop):
        pass

    def stop(self, shop):
        pass


@pytest.fixture
def owner():
    return Account.create()


@pytest.fixture
def hub(store, owner):
    circles = {"testnet": FakeCircle(), "mainnet": FakeCircle()}
    auth = Auth(lambda wallet: [SHOP] if wallet == owner.address else [],
                public_url="https://app.embco.xyz")
    app = create_app(store=store, auth=auth, units=Units(), circles=circles,
                     shops_of=lambda wallet: [SHOP] if wallet == owner.address else [],
                     limits_of=lambda shop: ShopLimits(Decimal(300), Decimal(2000), OTHER),
                     network_of=lambda shop: MAINNET,
                     rpc_urls={"testnet": "https://t.example.com",
                               "mainnet": "https://m.example.com"})
    client = TestClient(app, base_url="https://testserver")
    message = client.post("/api/owners/sign-in-request", json={"address": owner.address}).json()
    signature = Account.sign_message(encode_defunct(text=message["message"]),
                                     owner.key).signature.hex()
    assert client.post("/api/owners/sign-in", json={"nonce": message["nonce"],
                                                    "signature": signature}).status_code == 200
    return {"client": client, "circles": circles}


def test_a_new_shop_gets_its_agent_wallets_on_the_network_chosen(hub):
    client, circles = hub["client"], hub["circles"]
    made = client.post("/api/owners/agent-wallets", json={"network": "mainnet"}).json()
    assert made["network"] == "mainnet" and circles["mainnet"].blockchains == ["ARC", "ARC"]
    assert circles["testnet"].blockchains == []
    assert client.post("/api/owners/agent-wallets").json()["network"] == "testnet"
    assert circles["testnet"].blockchains == ["ARC-TESTNET", "ARC-TESTNET"]
    refused = client.post("/api/owners/agent-wallets", json={"network": "sepolia"})
    assert refused.status_code == 422


def test_there_is_no_draft_mode_on_mainnet(hub, store):
    store.save(config(network="mainnet"), creds())
    response = hub["client"].post(f"/api/shops/{SHOP}/draft-mode", json={"draft": True})
    assert response.status_code == 409 and "mainnet" in response.json()["error"]
    status = hub["client"].get(f"/api/shops/{SHOP}").json()
    assert status["network"] == "mainnet" and status["draft_mode"] is False


@pytest.mark.parametrize("path", ["/rpc", "/rpc/mainnet"])
def test_each_network_has_its_own_rpc_door(hub, path):
    answer = hub["client"].post(path, json={"jsonrpc": "2.0", "id": 1, "method": "debug_x"})
    assert answer.json()["error"]["message"] == "method not available here"


def test_stop_pauses_a_mainnet_contract_with_the_mainnet_circle_account(store):
    store.save(config(network="mainnet"), creds())
    circles = {"testnet": FakeCircle(), "mainnet": FakeCircle()}
    assert contract_pauser(circles, store)(SHOP) == "tx-1"
    assert circles["mainnet"].executed and not circles["testnet"].executed
