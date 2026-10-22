import secrets
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from eth_abi import decode
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import to_checksum_address
from fastapi.testclient import TestClient

from services.circle import CircleTransaction, CircleWallet
from services.earn.client import ARC_USDC
from services.hub.agent_balance import GAS_RESERVE, TRANSFER
from services.hub.api import create_app
from services.hub.auth import Auth
from services.hub.chain import ShopLimits
from services.shops import ErpCredentials, ShopConfig, ShopStore, new_key
from services.shops.activity import read_activity

SHOP = "0x" + "a1" * 20
AT = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


class FakeCircle:
    def __init__(self, account_type):
        self.wallet = CircleWallet(id="w-agent", address=Account.create().address,
                                   blockchain="ARC-TESTNET", wallet_set_id="set-1",
                                   account_type=account_type)
        self.sent = []

    def get_wallet(self, wallet_id):
        assert wallet_id == self.wallet.id
        return self.wallet

    def execute_contract(self, wallet_id, contract, call_data, *, idempotency_key, ref_id=None):
        self.sent.append((wallet_id, contract, call_data, ref_id))
        return CircleTransaction(id=f"tx-{len(self.sent)}", state="INITIATED")


class FakeUnits:
    def start(self, shop):
        pass

    def stop(self, shop):
        pass


def sign(account, message):
    return Account.sign_message(encode_defunct(text=message["message"]),
                                account.key).signature.hex()


@pytest.fixture
def owner():
    return Account.create()  # generated at run time: no key literal in the tests


def make_hub(tmp_path, owner, *, account_type="EOA", balance=Decimal("19.5435"), agent_on=False):
    store = ShopStore(tmp_path / "shops", new_key())
    store.save(ShopConfig(shop=SHOP, erp_url="https://shop.frappe.cloud", company="TEST Shop",
                          max_per_payment="250", weekly_budget="1200", agent_wallet_id="w-agent"),
               ErpCredentials(api_key=secrets.token_hex(8), api_secret=secrets.token_hex(16)))
    if not agent_on:
        store.set_agent_on(SHOP, False, AT.isoformat())
    circle = FakeCircle(account_type)
    auth = Auth(lambda wallet: [SHOP] if wallet == owner.address else [],
                public_url="https://app.embco.xyz")
    app = create_app(store=store, auth=auth, units=FakeUnits(), circle=circle,
                     limits_of=lambda shop: ShopLimits(Decimal(250), Decimal(1200),
                                                       circle.wallet.address),
                     balance_of=lambda wallet: balance if wallet == circle.wallet.address
                     else Decimal(0))
    return TestClient(app, base_url="https://testserver"), store, circle


def signed_in(client, account):
    message = client.post(f"/api/shops/{SHOP}/sign-in-request",
                          json={"address": account.address}).json()
    response = client.post(f"/api/shops/{SHOP}/sign-in",
                           json={"nonce": message["nonce"], "signature": sign(account, message)})
    assert response.status_code == 200, response.text
    return client


def sent_transfer(circle):
    (wallet_id, contract, call_data, ref_id), = circle.sent
    assert (wallet_id, contract, ref_id) == ("w-agent", ARC_USDC, "embco-return")
    assert call_data[:4] == TRANSFER
    to, units = decode(["address", "uint256"], call_data[4:])
    return to_checksum_address(to), units


def test_an_older_agent_gives_back_all_but_its_gas_to_the_owner(tmp_path, owner):
    client, store, circle = make_hub(tmp_path, owner)
    response = signed_in(client, owner).post(f"/api/shops/{SHOP}/agent-balance/return")
    assert response.status_code == 200, response.text
    to, units = sent_transfer(circle)
    assert to == owner.address
    assert Decimal(units).scaleb(-6) == Decimal("19.5435") - GAS_RESERVE
    assert response.json() == {"amount": "19.4435", "to": owner.address, "transaction": "tx-1"}
    event = read_activity(store.folder(SHOP))[-1]
    assert event["kind"] == "you" and "19.4435 USDC" in event["text"] and "tx-1" in event["text"]


def test_an_agent_whose_gas_circle_pays_gives_back_everything(tmp_path, owner):
    client, _, circle = make_hub(tmp_path, owner, account_type="SCA")
    response = signed_in(client, owner).post(f"/api/shops/{SHOP}/agent-balance/return")
    assert response.status_code == 200, response.text
    assert sent_transfer(circle) == (owner.address, 19_543_500)


def test_nothing_moves_while_the_agent_is_on(tmp_path, owner):
    client, _, circle = make_hub(tmp_path, owner, agent_on=True)
    response = signed_in(client, owner).post(f"/api/shops/{SHOP}/agent-balance/return")
    assert response.status_code == 409 and "turn the agent off" in response.json()["error"]
    assert circle.sent == []


def test_only_the_owner_can_take_it_back(tmp_path, owner):
    client, _, circle = make_hub(tmp_path, owner)
    assert client.post(f"/api/shops/{SHOP}/agent-balance/return").status_code == 401
    stranger = Account.create()
    message = client.post(f"/api/shops/{SHOP}/sign-in-request",
                          json={"address": stranger.address}).json()
    response = client.post(f"/api/shops/{SHOP}/sign-in",
                           json={"nonce": message["nonce"], "signature": sign(stranger, message)})
    assert response.status_code == 401
    assert client.post(f"/api/shops/{SHOP}/agent-balance/return").status_code == 401
    assert circle.sent == []


def test_a_balance_that_only_covers_the_gas_is_not_sent(tmp_path, owner):
    client, _, circle = make_hub(tmp_path, owner, balance=Decimal("0.05"))
    response = signed_in(client, owner).post(f"/api/shops/{SHOP}/agent-balance/return")
    assert response.status_code == 409 and "nothing to take back" in response.json()["error"]
    assert circle.sent == []


def test_the_owner_sees_what_the_agent_holds_and_what_can_come_back(tmp_path, owner):
    client, _, circle = make_hub(tmp_path, owner)
    response = signed_in(client, owner).get(f"/api/shops/{SHOP}/agent-balance")
    assert response.status_code == 200, response.text
    assert response.json() == {"wallet": circle.wallet.address, "balance": "19.5435",
                               "returnable": "19.4435", "gasless": False}
