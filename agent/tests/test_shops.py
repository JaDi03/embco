import json
import secrets

import pytest

from embco.cli import main
from embco.journal import SqliteJournal
from embco.ledger import LedgerError
from embco.shops import ErpCredentials, ShopConfig, ShopError, ShopStore, new_key, run_shop
from support import FakeLedger

SHOP_A = "0x" + "a1" * 20
SHOP_B = "0x" + "b2" * 20
CANARY = secrets.token_urlsafe(24)  # generated: no key literal in the tests


def creds(secret=None):
    return ErpCredentials(api_key=secrets.token_hex(8),
                          api_secret=secret or secrets.token_urlsafe(24))


def config(shop=SHOP_A, **kwargs):
    return ShopConfig(shop=shop, erp_url="https://erp.example.com", company="TEST Shop",
                      max_per_payment="5000", weekly_budget="2500", **kwargs)


@pytest.fixture
def store(tmp_path):
    return ShopStore(tmp_path / "shops", new_key())


def test_a_shop_is_saved_in_its_own_folder_with_its_keys_encrypted(store):
    store.save(config(), creds(CANARY))
    folder = store.folder(SHOP_A)
    assert CANARY.encode() not in (folder / "erp.enc").read_bytes()
    assert CANARY not in (folder / "config.json").read_text(encoding="utf-8")
    assert store.credentials(SHOP_A).api_secret == CANARY
    assert store.config(SHOP_A) == config()


def test_keys_cannot_be_read_with_another_service_key(store, tmp_path):
    store.save(config(), creds(CANARY))
    other = ShopStore(store.root, new_key())
    with pytest.raises(ShopError) as error:
        other.credentials(SHOP_A)
    assert CANARY not in str(error.value)


@pytest.mark.parametrize("shop", ["../etc", "0x" + "A1" * 20, "shop", "0x" + "a1" * 19])
def test_only_a_contract_address_names_a_shop_folder(store, shop):
    with pytest.raises(ShopError):
        store.folder(shop)


def test_each_shop_has_its_own_memory_and_payments_stay_off(store):
    secret_a, secret_b = creds(), creds()
    store.save(config(SHOP_A, wallet_bank="USDC on Arc", payment_extra={"payment_form": "03"}),
               secret_a)
    store.save(config(SHOP_B), secret_b)
    a, b = store.settings(SHOP_A), store.settings(SHOP_B)
    assert a.journal_path != b.journal_path
    assert a.journal_path.parent == store.folder(SHOP_A)
    assert (a.erpnext_api_secret, b.erpnext_api_secret) == (secret_a.api_secret,
                                                            secret_b.api_secret)
    assert a.erpnext_wallet_bank == "USDC on Arc" and b.erpnext_wallet_bank is None
    assert a.erpnext_payment_extra == {"payment_form": "03"}
    assert not a.pay and not a.explain


def test_the_process_environment_does_not_reach_a_shop(store, monkeypatch):
    monkeypatch.setenv("EMBCO_PAY", "on")
    monkeypatch.setenv("EMBCO_ERPNEXT_URL", "https://someone-else.example.com")
    store.save(config(), creds())
    settings = store.settings(SHOP_A)
    assert not settings.pay and settings.erpnext_url == "https://erp.example.com"


def platform():
    return {"CIRCLE_API_KEY": secrets.token_hex(8), "CIRCLE_ENTITY_SECRET": secrets.token_hex(8),
            "ARC_TESTNET_RPC_URL": "https://rpc.example.com"}


def test_a_shop_with_its_agent_wallet_pays_on_testnet_and_leaves_drafts(store):
    store.save(config(agent_wallet_id="wallet-a"), creds())
    shared = platform()
    settings = store.settings(SHOP_A, shared)
    assert settings.pay and settings.erpnext_draft_payments
    assert settings.shop_address == SHOP_A and settings.agent_wallet_id == "wallet-a"
    assert settings.circle_api_key == shared["CIRCLE_API_KEY"]


@pytest.mark.parametrize("missing", ["CIRCLE_API_KEY", "CIRCLE_ENTITY_SECRET",
                                     "ARC_TESTNET_RPC_URL"])
def test_without_the_platform_accounts_a_shop_only_observes(store, missing):
    store.save(config(agent_wallet_id="wallet-a"), creds())
    shared = platform() | {missing: ""}
    assert not store.settings(SHOP_A, shared).pay


def test_a_shop_without_its_agent_wallet_only_observes(store):
    store.save(config(), creds())
    settings = store.settings(SHOP_A, platform())
    assert not settings.pay and settings.erpnext_draft_payments


def test_the_shop_loop_gets_a_payer_when_payments_are_on(store):
    store.save(config(agent_wallet_id="wallet-a"), creds())
    seen = []
    run_shop(store, SHOP_A, cycles=1, erp=lambda settings: FakeLedger(), platform=platform(),
             payer=lambda settings, ledger: seen.append(settings.pay))
    assert seen == [True]


def test_disconnecting_forgets_the_keys_and_keeps_the_memory(store):
    store.save(config(), creds())
    run_shop(store, SHOP_A, cycles=1, erp=lambda settings: FakeLedger())
    assert store.connected() == [SHOP_A]
    store.disconnect(SHOP_A)
    assert store.connected() == []
    assert (store.folder(SHOP_A) / "journal.sqlite3").exists()
    with pytest.raises(ShopError, match="not connected"):
        store.settings(SHOP_A)


def test_a_cycle_leaves_a_summary_for_the_dashboard(store):
    store.save(config(), creds())
    assert run_shop(store, SHOP_A, cycles=1, erp=lambda settings: FakeLedger()) == 1
    summary = store.last_run(SHOP_A)
    assert summary["ok"] and summary["run_id"] == 1
    [decision] = summary["decisions"]
    assert decision["invoice"] == "PINV-1" and decision["action"] in {"PAY", "HOLD", "ASK"}
    assert sum(summary["counts"].values()) == 1
    with SqliteJournal(store.folder(SHOP_A) / "journal.sqlite3") as journal:
        journal.verify()


def test_an_erp_error_is_left_for_the_dashboard_and_the_loop_goes_on(store):
    class Down(FakeLedger):
        def list_unpaid_purchase_invoices(self):
            raise LedgerError("ERPNext returned HTTP 401 for /api/resource/Purchase%20Invoice")

    store.save(config(), creds(CANARY))
    assert run_shop(store, SHOP_A, cycles=2, sleep=lambda s: None,
                    erp=lambda settings: Down()) == 0
    summary = store.last_run(SHOP_A)
    assert not summary["ok"] and "401" in summary["error"]
    assert CANARY not in json.dumps(summary)


def test_the_shop_command_refuses_a_folder_that_is_not_a_shop(tmp_path, capsys):
    key = tmp_path / "shops.key"
    key.write_bytes(new_key())
    folder = tmp_path / "shops" / "not-a-shop"
    assert main(["shop", "--dir", str(folder), "--key-file", str(key), "--cycles", "1"]) == 2
    assert "contract address" in capsys.readouterr().err


def test_the_shop_command_needs_a_readable_service_key(tmp_path, capsys):
    folder = tmp_path / "shops" / SHOP_A
    assert main(["shop", "--dir", str(folder), "--key-file", str(tmp_path / "missing.key")]) == 2
    assert "service key" in capsys.readouterr().err
