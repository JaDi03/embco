from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.shops.balances import (
    balance_point,
    money_changed,
    read_balances,
    record_balance,
    remember_seen,
)

AT = datetime(2026, 10, 20, 14, 56, tzinfo=UTC)
# lopezgrocery's last_run.json on 2026-10-20 14:56 UTC, trimmed to what the point uses
EARN = {"in_wallet": "1.004041", "total_in_earn": "6.689999"}
UNIFIED = {
    "chains": [
        {"chain": "Base_Sepolia", "available": "0.001313", "arriving": "0.000000"},
        {"chain": "Ethereum_Sepolia", "available": "6.959198", "arriving": "0.000000"},
    ],
    "wallets": [
        {"chain": "Base_Sepolia", "in_wallet": "0.000000"},
        {"chain": "Ethereum_Sepolia", "in_wallet": "6965.000000"},
    ],
}


def test_a_point_adds_gateway_and_the_owner_wallets_on_other_chains():
    assert balance_point(AT, EARN, UNIFIED) == {
        "at": AT.isoformat(), "wallet": "1.004041", "earn": "6.689999",
        "other_chains": "6971.960511"}


def test_no_point_when_a_place_could_not_be_read():
    assert balance_point(AT, None, UNIFIED) is None
    assert balance_point(AT, {"error": "node down"}, UNIFIED) is None
    assert balance_point(AT, EARN, {"error": "gateway down"}) is None
    assert balance_point(AT, EARN, {**UNIFIED, "wallets": {"error": "rpc down"}}) is None


def test_other_chains_count_zero_when_not_connected():
    assert balance_point(AT, EARN, None)["other_chains"] == "0"


def test_a_point_is_kept_when_a_number_changed_or_an_hour_passed(tmp_path):
    first = balance_point(AT, EARN, UNIFIED)
    record_balance(tmp_path, first)
    record_balance(tmp_path, balance_point(AT + timedelta(minutes=15), EARN, UNIFIED))
    assert len(read_balances(tmp_path)) == 1
    record_balance(tmp_path, balance_point(AT + timedelta(minutes=30),
                                           {**EARN, "total_in_earn": "131.69"}, UNIFIED))
    record_balance(tmp_path, balance_point(AT + timedelta(minutes=95),
                                           {**EARN, "total_in_earn": "131.69"}, UNIFIED))
    assert [p["earn"] for p in read_balances(tmp_path)] == ["6.689999", "131.69", "131.69"]
    record_balance(tmp_path, None)
    assert len(read_balances(tmp_path)) == 3


def test_unreadable_lines_are_skipped(tmp_path):
    (tmp_path / "balances.jsonl").write_text('not json\n{"at": "x"}\n', encoding="utf-8")
    assert read_balances(tmp_path) == []


def test_money_wakes_the_agent_once_per_change_above_the_threshold(tmp_path):
    threshold = Decimal("120")  # a tenth of lopezgrocery's weekly budget, 1200
    record_balance(tmp_path, balance_point(AT, EARN, UNIFIED))
    assert money_changed(tmp_path, threshold) is None  # no baseline yet
    remember_seen(tmp_path)
    assert money_changed(tmp_path, threshold) is None  # nothing moved

    more = {**UNIFIED, "wallets": [{"chain": "Ethereum_Sepolia", "in_wallet": "11965"}]}
    record_balance(tmp_path, balance_point(AT + timedelta(minutes=15), EARN, more))
    moved = money_changed(tmp_path, threshold)
    assert "other chains" in moved and "6971.960511 -> 11971.960511" in moved
    assert "Earn" not in moved

    remember_seen(tmp_path)  # the agent looked and chose to leave it there
    assert money_changed(tmp_path, threshold) is None


def test_small_moves_do_not_wake_the_agent(tmp_path):
    record_balance(tmp_path, balance_point(AT, EARN, UNIFIED))
    remember_seen(tmp_path)
    record_balance(tmp_path, balance_point(AT + timedelta(hours=2),
                                           {**EARN, "total_in_earn": "6.71"}, UNIFIED))
    assert money_changed(tmp_path, Decimal("120")) is None
