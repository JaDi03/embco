import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from agent.guardrails.rules import PolicyConfig
from agent.memory import SqliteJournal
from agent.reflexes.cycle import run_cycle
from services.erp import LedgerError
from services.payments import PaymentEvent, PaymentStatus
from services.shops.inbox import INBOX, take_signatures
from services.shops.supplier_view import (
    PAYMENT_SENT,
    SCHEDULED,
    SIGNATURE_NEEDED,
    UNDER_REVIEW,
    supplier_view,
)
from support import FakeLedger, make_invoice

SUPPLIER_KEY = Account.create()  # the supplier's new wallet, fresh on every run
MONDAY = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
POLICY = PolicyConfig(max_per_payment=Decimal(5000), weekly_budget=Decimal(2500))
TX = "0x" + "e7" * 32


@pytest.fixture
def journal(tmp_path):
    with SqliteJournal(tmp_path / "journal.sqlite3") as j:
        yield j


def changed_wallet() -> FakeLedger:
    ledger = FakeLedger()
    ledger.supplier = ledger.supplier.model_copy(update={"wallet_address": SUPPLIER_KEY.address})
    return ledger


def view(ledger, journal, signatures=None):
    report = run_cycle(ledger, journal, POLICY, "TEST Shop", at=MONDAY)
    return supplier_view(report, journal, "TEST Shop", signatures)


def paid(invoice, status, tx=TX):
    return PaymentEvent(invoice=invoice, status=status, at=MONDAY, supplier="S",
                        payee="0x" + "a1" * 20, amount=Decimal("1000"),
                        invoice_ref="0x" + "00" * 32, tx_hash=tx)


def test_an_invoice_the_agent_will_pay_is_shown_as_scheduled(journal):
    [invoice] = view(FakeLedger(), journal)["suppliers"]["S"]["invoices"]
    assert invoice == {"invoice": "PINV-1", "amount": "1000", "due_date": "2026-10-10",
                       "status": SCHEDULED}


def test_a_held_invoice_is_under_review_without_the_shops_reasons(journal):
    ledger = FakeLedger()
    ledger.pending = [make_invoice(qty="12")]  # more than was received: the agent holds it
    report = run_cycle(ledger, journal, POLICY, "TEST Shop", at=MONDAY)
    page = supplier_view(report, journal, "TEST Shop")["suppliers"]["S"]
    assert [i["status"] for i in page["invoices"]] == [UNDER_REVIEW]
    text = json.dumps(page)
    assert report.decisions[0].reasons and not any(r in text for r in report.decisions[0].reasons)


def test_a_new_wallet_asks_the_supplier_to_sign_the_exact_challenge(journal):
    page = view(changed_wallet(), journal)["suppliers"]["S"]
    assert [i["status"] for i in page["invoices"]] == [SIGNATURE_NEEDED]
    challenge = page["challenge"]
    assert challenge["wallet"] == SUPPLIER_KEY.address
    assert challenge["typed_data"]["primaryType"] == "WalletOwnership"
    assert challenge["typed_data"]["message"]["payer"] == "TEST Shop"


def test_payments_are_listed_with_their_transaction(journal):
    journal.record_payment(paid("PINV-1", PaymentStatus.SUBMITTED, tx=None))
    page = view(FakeLedger(), journal)["suppliers"]["S"]
    assert [i["status"] for i in page["invoices"]] == [PAYMENT_SENT]
    journal.record_payment(paid("PINV-1", PaymentStatus.RECORDED))
    page = view(FakeLedger(), journal)["suppliers"]["S"]
    assert page["invoices"] == []
    assert page["payments"] == [{"invoice": "PINV-1", "amount": "1000",
                                 "paid_at": MONDAY.isoformat(), "tx_hash": TX,
                                 "wallet": "0x" + "a1" * 20}]


def test_a_supplier_sees_only_its_own_entry(journal):
    journal.record_payment(replace(paid("PINV-9", PaymentStatus.COMPLETE), supplier="Other"))
    suppliers = view(FakeLedger(), journal)["suppliers"]
    assert set(suppliers) == {"S", "Other"}
    assert all(p["invoice"] == "PINV-9" for p in suppliers["Other"]["payments"])
    assert suppliers["S"]["payments"] == []


# The inbox


def sign(typed: dict, key=SUPPLIER_KEY) -> str:
    signable = encode_typed_data(full_message=typed)
    return "0x" + key.sign_message(signable).signature.hex().removeprefix("0x")


def drop(folder, supplier, signature, name="a.json"):
    (folder / INBOX).mkdir(exist_ok=True)
    (folder / INBOX / name).write_text(json.dumps({"supplier": supplier,
                                                   "signature": signature}))


def test_a_signature_from_the_page_proves_the_wallet(journal, tmp_path):
    ledger = changed_wallet()
    typed = view(ledger, journal)["suppliers"]["S"]["challenge"]["typed_data"]
    drop(tmp_path, "S", sign(typed))
    results = take_signatures(tmp_path, journal, ledger, at=MONDAY + timedelta(hours=1))
    assert results["S"]["result"] == "ACCEPTED"
    assert journal.latest_wallet_proof("S").wallet == SUPPLIER_KEY.address
    assert list((tmp_path / INBOX).iterdir()) == []
    page = view(ledger, journal, results)["suppliers"]["S"]
    assert page["challenge"] is None and page["last_signature"]["result"] == "ACCEPTED"


def test_a_signature_from_another_wallet_is_rejected(journal, tmp_path):
    ledger = changed_wallet()
    typed = view(ledger, journal)["suppliers"]["S"]["challenge"]["typed_data"]
    drop(tmp_path, "S", sign(typed, Account.create()))
    results = take_signatures(tmp_path, journal, ledger, at=MONDAY + timedelta(hours=1))
    assert results["S"]["result"] == "REJECTED"
    assert journal.latest_wallet_proof("S") is None
    assert list((tmp_path / INBOX).iterdir()) == []


def test_a_signature_waits_while_the_erp_is_down(journal, tmp_path):
    class Down(FakeLedger):
        def get_supplier(self, name):
            raise LedgerError("ERPNext returned HTTP 502")

    view(changed_wallet(), journal)
    drop(tmp_path, "S", "0x00")
    assert take_signatures(tmp_path, journal, Down(), at=MONDAY) == {}
    assert len(list((tmp_path / INBOX).iterdir())) == 1


def test_an_unreadable_file_is_dropped(journal, tmp_path):
    (tmp_path / INBOX).mkdir()
    (tmp_path / INBOX / "x.json").write_text("not json")
    assert take_signatures(tmp_path, journal, FakeLedger(), at=MONDAY) == {}
    assert list((tmp_path / INBOX).iterdir()) == []
