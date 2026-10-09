import secrets
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from agent.guardrails.rules import PolicyConfig
from agent.memory import SqliteJournal
from agent.reflexes.cycle import run_cycle
from services.hub.api import create_app
from services.hub.chain import ShopLimits
from services.payments import PaymentEvent, PaymentStatus, Settlement
from services.shops import ErpCredentials, ShopConfig, ShopStore, activity, new_key, run_shop
from support import WALLET_A, FakeLedger
from test_hub import AGENT_ON_CONTRACT, SHOP, auth, clock, owner, sign  # noqa: F401

MONDAY = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
POLICY = PolicyConfig(max_per_payment=Decimal(5000), weekly_budget=Decimal(2500))
TX = "0x" + "ab" * 32


def kinds(events):
    return [e["kind"] for e in events]


@pytest.fixture
def store(tmp_path):
    s = ShopStore(tmp_path / "shops", new_key())
    s.save(ShopConfig(shop=SHOP, erp_url="https://shop.frappe.cloud", company="TEST Shop",
                      max_per_payment="5000", weekly_budget="2500"),
           ErpCredentials(api_key=secrets.token_hex(8), api_secret=secrets.token_urlsafe(24)))
    return s


def test_each_check_is_written_as_numbered_events(store):
    run_shop(store, SHOP, cycles=2, sleep=lambda s: None, erp=lambda settings: FakeLedger())
    events = activity.read_activity(store.folder(SHOP))
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    # the agent is woken but no model answers in tests: nothing is paid, and the next check
    # waits before waking it again
    assert kinds(events) == ["check", "reflex", "error", "new", "done", "check", "done"]
    assert "Nothing new is paid" in events[2]["text"]
    assert "New invoice PINV-1 from S, 1000 USDC: the checks pass" in events[3]["text"]
    assert events[4]["text"].startswith("Check done: 1 unpaid")
    assert "Next check in 15 minutes" in events[4]["text"]
    assert [e["seq"] for e in activity.read_activity(store.folder(SHOP), after=5)] == [6, 7]


def test_a_failed_check_is_told_to_the_owner(store):
    from services.erp import LedgerError

    class Down(FakeLedger):
        def list_unpaid_purchase_invoices(self):
            raise LedgerError("ERPNext returned HTTP 502 for /api/resource/Purchase%20Invoice")

    run_shop(store, SHOP, cycles=1, erp=lambda settings: Down())
    events = activity.read_activity(store.folder(SHOP))
    assert kinds(events) == ["check", "error"]
    assert "502" in events[1]["text"] and "tries again" in events[1]["text"]


def test_payments_show_with_their_transaction(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        report = run_cycle(FakeLedger(), journal, POLICY, "TEST Shop", at=MONDAY)
    paid = PaymentEvent(invoice="PINV-1", status=PaymentStatus.COMPLETE, at=MONDAY, supplier="S",
                        payee=WALLET_A, amount=Decimal(1000), invoice_ref="0x" + "00" * 32,
                        tx_hash=TX)
    recorded = replace(paid, status=PaymentStatus.RECORDED, erp_entry="ACC-PAY-1")
    blocked = replace(paid, status=PaymentStatus.BLOCKED, tx_hash=None, reason="not approved")
    report = replace(report, settlement=Settlement(events=(paid, recorded, blocked)))
    events = activity.cycle_events(report, 15)
    by_kind = {e["kind"]: e for e in events}
    assert by_kind["paid"]["text"] == "Paid 1000 USDC to S for PINV-1."
    assert by_kind["paid"]["tx"] == TX
    assert by_kind["recorded"]["text"] == "PINV-1 recorded in ERPNext as ACC-PAY-1."
    assert by_kind["not_paid"]["text"] == "Did not pay PINV-1: not approved."
    assert "1000 USDC paid" in by_kind["done"]["text"]


def test_signatures_answers_and_limits_are_told_once(tmp_path):
    at = MONDAY
    events = activity.inbox_events(
        at, {"S": {"result": "ACCEPTED", "wallet": WALLET_A}},
        {"PINV-1": {"result": "REJECTED", "verdict": "APPROVE", "reason": "the question changed"}})
    assert events[0]["text"] == "S confirmed its wallet 0xa1a1...a1a1 by signing."
    assert events[1]["text"] == "Your approval of PINV-1 was not used: the question changed."
    assert activity.limits_read(at, None, POLICY)[0]["text"].startswith(
        "Limits from your contract: 5000 USDC per payment")
    assert activity.limits_read(at, POLICY, POLICY) == []


def test_only_the_newest_events_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "MAX_EVENTS", 3)
    for _ in range(5):
        activity.append_activity(tmp_path, [activity.check_started(MONDAY)])
    assert [e["seq"] for e in activity.read_activity(tmp_path)] == [3, 4, 5]


# ---- the hub route


@pytest.fixture
def client(store, auth, owner):  # noqa: F811
    activity.append_activity(store.folder(SHOP), [activity.check_started(MONDAY)] * 3)
    app = create_app(store=store, auth=auth, units=None,
                     limits_of=lambda s: ShopLimits(Decimal(1), Decimal(1), AGENT_ON_CONTRACT))
    c = TestClient(app, base_url="https://testserver")
    return c


def test_the_owner_reads_new_events_only(client, owner):  # noqa: F811
    message = client.post(f"/api/shops/{SHOP}/sign-in-request").json()
    client.post(f"/api/shops/{SHOP}/sign-in", json={
        "nonce": message["message"]["nonce"], "signature": sign(owner, message)})
    body = client.get(f"/api/shops/{SHOP}/activity", params={"after": 1}).json()
    assert [e["seq"] for e in body["events"]] == [2, 3]
    assert body["interval_minutes"] == 15


def test_the_activity_needs_the_owner(client):
    assert client.get(f"/api/shops/{SHOP}/activity").status_code == 401


# ---- the story before the feed existed


def test_the_feed_gets_the_agents_past_once_before_what_is_new(store, tmp_path):
    run_shop(store, SHOP, cycles=1, erp=lambda settings: FakeLedger())
    folder = store.folder(SHOP)
    with SqliteJournal(folder / "journal.sqlite3") as journal:
        journal.record_payment(PaymentEvent(
            invoice="PINV-1", status=PaymentStatus.COMPLETE, at=datetime.now(UTC), supplier="S",
            payee=WALLET_A, amount=Decimal(1000), invoice_ref="0x" + "00" * 32, tx_hash=TX))
        entries = journal.entries()
    (folder / activity.ACTIVITY).unlink()  # as on a shop that ran before the feed existed
    later = datetime(2030, 1, 1, tzinfo=UTC)
    activity.append_activity(folder, [activity.check_started(later)])
    assert activity.backfill(folder, entries) > 0
    events = activity.read_activity(folder)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert events[-1]["kind"] == "check" and not events[-1].get("history")
    past = [e for e in events if e.get("history")]
    assert {"limits", "new", "paid"} <= set(kinds(past))
    paid = next(e for e in past if e["kind"] == "paid")
    assert paid["text"] == "Paid 1000 USDC to S for PINV-1." and paid["tx"] == TX
    assert activity.backfill(folder, entries) == 0  # only once


def test_the_past_keeps_its_own_times_and_order(store):
    run_shop(store, SHOP, cycles=1, erp=lambda settings: FakeLedger())
    with SqliteJournal(store.folder(SHOP) / "journal.sqlite3") as journal:
        events = activity.history_events(journal.entries())
    assert [e["at"] for e in events] == sorted(e["at"] for e in events)
    assert events[0]["kind"] == "limits"
