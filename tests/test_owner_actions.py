import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from agent.guardrails.rules import PolicyConfig, Verdict
from agent.memory import SqliteJournal
from agent.reflexes.cycle import run_cycle
from services.hub.api import create_app
from services.hub.chain import ShopLimits
from services.shops import ErpCredentials, ShopConfig, ShopStore, new_key
from services.shops.activity import read_activity
from services.shops.agent import summarize
from services.shops.inbox import ANSWERS, INBOX, drop_answer, take_answers
from services.shops.owner_view import (
    FIRST_PAYMENT,
    ORDER_RECEIPT,
    OVER_LIMIT,
    SUPPLIER_SIGNATURE,
    WALLET_MISSING,
    decision_view,
)
from support import WALLET_B, FakeLedger, make_invoice
from test_hub import AGENT_ON_CONTRACT, SHOP, auth, clock, owner, sign  # noqa: F401

MONDAY = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
LOW_LIMIT = PolicyConfig(max_per_payment=Decimal(500), weekly_budget=Decimal(2500))


@pytest.fixture
def journal(tmp_path):
    with SqliteJournal(tmp_path / "journal.sqlite3") as j:
        yield j


def decide(ledger, journal, policy=LOW_LIMIT, at=MONDAY):
    report = run_cycle(ledger, journal, policy, "TEST Shop", at=at)
    return summarize(report, policy=policy)


def steps(summary):
    [d] = summary["decisions"]
    return {f["step"] for f in d["findings"]}


# ---- what the owner's page is told


def test_an_invoice_over_the_limit_asks_with_a_limit_step_and_the_limits(journal):
    summary = decide(FakeLedger(), journal)  # 1000 against a limit of 500
    [d] = summary["decisions"]
    assert d["action"] == "ASK" and steps(summary) == {OVER_LIMIT}
    assert len(d["fingerprint"]) == 64
    assert summary["limits"] == {"max_per_payment": "500", "weekly_cap": "2500"}


def test_wallet_steps_say_what_is_missing(journal):
    ledger = FakeLedger()
    ledger.supplier = ledger.supplier.model_copy(update={"wallet_address": None})
    assert WALLET_MISSING in steps(decide(ledger, journal))
    ledger.supplier = ledger.supplier.model_copy(update={"wallet_address": WALLET_B})
    assert SUPPLIER_SIGNATURE in steps(decide(ledger, journal))


def test_a_proven_new_wallet_is_a_first_payment_step(journal):
    from agent.guardrails.controls import WalletProof

    ledger = FakeLedger()
    ledger.supplier = ledger.supplier.model_copy(update={"wallet_address": WALLET_B})
    journal.record_wallet_proof(WalletProof(supplier="S", wallet=WALLET_B, nonce="n",
                                            signature="0x", signed_at=MONDAY))
    assert steps(decide(ledger, journal)) >= {FIRST_PAYMENT}


def test_an_invoice_without_order_and_receipt_is_an_order_receipt_step(journal):
    ledger = FakeLedger()
    ledger.pending = [make_invoice(qty="12")]
    assert ORDER_RECEIPT in steps(decide(ledger, journal))


def test_the_page_sees_where_the_payment_is(journal):
    from services.payments import PaymentEvent, PaymentStatus

    report = run_cycle(FakeLedger(), journal, LOW_LIMIT, "TEST Shop", at=MONDAY)
    event = PaymentEvent(invoice="PINV-1", status=PaymentStatus.RECORDED, at=MONDAY,
                         supplier="S", payee=WALLET_B, amount=Decimal(1000),
                         invoice_ref="0x" + "00" * 32, tx_hash="0x" + "ab" * 32,
                         erp_entry="ACC-PAY-1")
    view = decision_view(report.decisions[0], event)
    assert view["payment"] == {"status": "RECORDED", "tx_hash": "0x" + "ab" * 32,
                               "erp_entry": "ACC-PAY-1", "reason": ""}


# ---- the owner's answers, through the inbox


def test_an_approval_for_the_question_shown_lets_the_agent_pay(journal, tmp_path):
    [d] = decide(FakeLedger(), journal)["decisions"]
    drop_answer(tmp_path, "PINV-1", Verdict.APPROVE, d["fingerprint"], "owner 0xabc", "", MONDAY)
    results = take_answers(tmp_path, journal, at=MONDAY)
    assert results["PINV-1"]["result"] == "ACCEPTED"
    assert list((tmp_path / INBOX / ANSWERS).iterdir()) == []
    [after] = decide(FakeLedger(), journal, at=MONDAY + timedelta(minutes=15))["decisions"]
    assert after["action"] == "PAY" and after["reasons"][0].startswith("approved by owner 0xabc")


def test_an_answer_to_a_question_that_changed_is_refused(journal, tmp_path):
    decide(FakeLedger(), journal)
    drop_answer(tmp_path, "PINV-1", Verdict.APPROVE, "0" * 64, "owner 0xabc", "", MONDAY)
    results = take_answers(tmp_path, journal, at=MONDAY)
    assert results["PINV-1"]["result"] == "REJECTED" and "changed" in results["PINV-1"]["reason"]
    [after] = decide(FakeLedger(), journal, at=MONDAY + timedelta(minutes=15))["decisions"]
    assert after["action"] == "ASK"


def test_a_rejection_holds_the_invoice_with_the_owners_note(journal, tmp_path):
    [d] = decide(FakeLedger(), journal)["decisions"]
    drop_answer(tmp_path, "PINV-1", Verdict.REJECT, d["fingerprint"], "owner 0xabc",
                "supplier did not ask for this", MONDAY)
    take_answers(tmp_path, journal, at=MONDAY)
    [after] = decide(FakeLedger(), journal, at=MONDAY + timedelta(minutes=15))["decisions"]
    assert after["action"] == "HOLD" and "supplier did not ask for this" in after["reasons"][0]


# ---- the hub routes


class Units:
    def __init__(self):
        self.started, self.stopped = [], []

    def start(self, shop):
        self.started.append(shop)

    def stop(self, shop):
        self.stopped.append(shop)


@pytest.fixture
def hub(tmp_path, auth, journal):  # noqa: F811
    store = ShopStore(tmp_path / "shops", new_key())
    store.save(ShopConfig(shop=SHOP, erp_url="https://shop.frappe.cloud", company="TEST Shop",
                          max_per_payment="500", weekly_budget="2500"),
               ErpCredentials(api_key=secrets.token_hex(8), api_secret=secrets.token_urlsafe(24)))
    store.write_last_run(SHOP, decide(FakeLedger(), journal))
    units = Units()
    app = create_app(store=store, auth=auth, units=units,
                     limits_of=lambda s: ShopLimits(Decimal(500), Decimal(2500), AGENT_ON_CONTRACT))
    return {"app": app, "store": store, "units": units}


def owner_client(hub, owner):  # noqa: F811
    client = TestClient(hub["app"], base_url="https://testserver")
    message = client.post(f"/api/shops/{SHOP}/sign-in-request").json()
    response = client.post(f"/api/shops/{SHOP}/sign-in", json={
        "nonce": message["message"]["nonce"], "signature": sign(owner, message)})
    assert response.status_code == 200, response.text
    return client


def shown(hub):
    [d] = hub["store"].last_run(SHOP)["decisions"]
    return d


def test_the_owner_answers_the_question_it_sees(hub, owner):  # noqa: F811
    client = owner_client(hub, owner)
    response = client.post(f"/api/shops/{SHOP}/answers", json={
        "invoice": "PINV-1", "verdict": "APPROVE", "fingerprint": shown(hub)["fingerprint"]})
    assert response.status_code == 200, response.text
    assert client.get(f"/api/shops/{SHOP}").json()["answers_waiting"] == ["PINV-1"]


def test_an_answer_to_an_old_question_is_refused_at_once(hub, owner):  # noqa: F811
    response = owner_client(hub, owner).post(f"/api/shops/{SHOP}/answers", json={
        "invoice": "PINV-1", "verdict": "APPROVE", "fingerprint": "0" * 64})
    assert response.status_code == 409 and "changed" in response.json()["error"]


def test_only_a_question_can_be_answered(hub, owner):  # noqa: F811
    response = owner_client(hub, owner).post(f"/api/shops/{SHOP}/answers", json={
        "invoice": "PINV-404", "verdict": "APPROVE", "fingerprint": "0" * 64})
    assert response.status_code == 409


def test_answers_need_the_owner(hub):
    client = TestClient(hub["app"], base_url="https://testserver")
    response = client.post(f"/api/shops/{SHOP}/answers", json={
        "invoice": "PINV-1", "verdict": "APPROVE", "fingerprint": shown(hub)["fingerprint"]})
    assert response.status_code == 401
    assert client.post(f"/api/shops/{SHOP}/check").status_code == 401


def test_check_now_restarts_the_agent_but_not_twice_in_a_row(hub, owner):  # noqa: F811
    client = owner_client(hub, owner)
    assert client.post(f"/api/shops/{SHOP}/check").status_code == 200
    assert client.post(f"/api/shops/{SHOP}/check").status_code == 429
    assert hub["units"].started == [SHOP]


# ---- turning the whole agent on and off


def test_turning_the_agent_off_stops_everything_until_the_owner_turns_it_on(hub, owner):  # noqa: F811
    client = owner_client(hub, owner)
    assert client.post(f"/api/shops/{SHOP}/agent", json={"on": False}).json() == {
        "agent_on": False}
    assert hub["units"].stopped == [SHOP]
    assert client.get(f"/api/shops/{SHOP}").json()["agent_on"] is False
    assert client.post(f"/api/shops/{SHOP}/check").status_code == 409  # "check now" refused
    events = read_activity(hub["store"].folder(SHOP))
    assert events[-1]["kind"] == "you" and "no Claude" in events[-1]["text"]

    assert client.post(f"/api/shops/{SHOP}/agent", json={"on": True}).json() == {
        "agent_on": True}
    assert hub["units"].started == [SHOP]
    assert client.get(f"/api/shops/{SHOP}").json()["agent_on"] is True


def test_only_the_owner_turns_the_agent_on_or_off(hub):
    client = TestClient(hub["app"], base_url="https://testserver")
    assert client.post(f"/api/shops/{SHOP}/agent", json={"on": False}).status_code == 401
    assert hub["units"].stopped == [] and hub["store"].agent_on(SHOP)


def test_an_agent_turned_off_runs_no_cycle_even_if_its_process_starts(hub):
    from services.shops import run_shop

    store = hub["store"]
    store.set_agent_on(SHOP, False, MONDAY.isoformat())
    calls = []

    def erp(settings):
        calls.append(settings)
        return FakeLedger()

    run_shop(store, SHOP, cycles=1, sleep=lambda s: None, erp=erp)
    assert store.last_run(SHOP)["run_id"] == 1  # still the summary written before, untouched
    assert not any(e["kind"] == "check" for e in read_activity(store.folder(SHOP)))
