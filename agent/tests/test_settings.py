from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from embco.settings import Settings, SettingsError, read_env_file

CANARY = "canary-value-that-must-not-leak"
ENV = {
    "EMBCO_ERPNEXT_URL": "https://erp.example.com",
    "EMBCO_ERPNEXT_API_KEY": "key",
    "EMBCO_ERPNEXT_API_SECRET": CANARY,
    "EMBCO_COMPANY": "TEST Shop",
    "EMBCO_MAX_PER_PAYMENT": "500000",
    "EMBCO_WEEKLY_BUDGET": "300000.50",
}


def test_settings_come_from_the_environment_with_defaults():
    settings = Settings.from_env(ENV)
    assert settings.company == "TEST Shop"
    assert settings.policy.weekly_budget == Decimal("300000.50")
    assert settings.policy.max_price_increase == Decimal("0.15")
    assert settings.journal_path == Path("data/journal.sqlite3")
    assert settings.interval == timedelta(minutes=15)


def test_missing_settings_are_listed_together():
    env = {k: v for k, v in ENV.items() if k not in ("EMBCO_COMPANY", "EMBCO_WEEKLY_BUDGET")}
    with pytest.raises(SettingsError, match="EMBCO_COMPANY, EMBCO_WEEKLY_BUDGET"):
        Settings.from_env(env)


@pytest.mark.parametrize("value", ["abc", "0", "-5", "NaN", "Infinity"])
def test_money_settings_must_be_positive_numbers(value):
    with pytest.raises(SettingsError, match="EMBCO_MAX_PER_PAYMENT"):
        Settings.from_env({**ENV, "EMBCO_MAX_PER_PAYMENT": value})


@pytest.mark.parametrize("value", ["0", "1.5", "ten"])
def test_the_interval_must_be_whole_minutes(value):
    with pytest.raises(SettingsError, match="INTERVAL_MINUTES"):
        Settings.from_env({**ENV, "EMBCO_INTERVAL_MINUTES": value})


def test_secrets_never_show_in_repr():
    assert CANARY not in repr(Settings.from_env(ENV))


def test_the_env_file_fills_only_what_the_environment_does_not_set(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    lines = [f"{k}={v}" for k, v in ENV.items()]
    env_file.write_text("# comment\n\n" + "\n".join(lines) + '\nEMBCO_COMPANY="From File"\n')
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert read_env_file(env_file)["EMBCO_COMPANY"] == "From File"
    assert Settings.load(env_file).company == "From File"
    monkeypatch.setenv("EMBCO_COMPANY", "From Environment")
    assert Settings.load(env_file).company == "From Environment"


def test_payments_are_off_by_default_and_need_everything_when_on():
    assert Settings.from_env(ENV).pay is False
    with pytest.raises(SettingsError, match="CIRCLE_API_KEY, CIRCLE_ENTITY_SECRET"):
        Settings.from_env({**ENV, "EMBCO_PAY": "on"})


def test_payment_settings_hide_secrets_and_check_the_shop_address():
    env = {**ENV, "EMBCO_PAY": "on", "CIRCLE_API_KEY": CANARY, "CIRCLE_ENTITY_SECRET": CANARY,
           "EMBCO_AGENT_WALLET_ID": "w-1", "ARC_TESTNET_RPC_URL": "https://node/" + CANARY,
           "EMBCO_SHOP_ADDRESS": "0x" + "5c" * 20}
    settings = Settings.from_env(env)
    assert settings.pay and CANARY not in repr(settings)
    with pytest.raises(SettingsError, match="SHOP_ADDRESS"):
        Settings.from_env({**env, "EMBCO_SHOP_ADDRESS": "shop"})
