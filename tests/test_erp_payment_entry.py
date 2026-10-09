import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from services.erp import ErpnextAdapter, LedgerError
from services.erp.models import SettledPayment

BASE = "https://erp.test"
TX = "0x" + "e7" * 32
WALLET = "0x" + "21" * 20
PAYMENT = SettledPayment(invoice="PINV-1", supplier="Acme", amount=Decimal("125.5"),
                         paid_on=date(2026, 10, 7), tx_hash=TX, payee_wallet=WALLET,
                         note="PAY decided by the embco agent")
# Raw text on purpose: amounts must reach the adapter exactly as the ERP wrote them.
DRAFT = """{"message": {"doctype": "Payment Entry", "payment_type": "Pay", "party": "Acme",
  "paid_from": "Cash - TS", "paid_from_account_currency": "USD", "paid_to": "Creditors - TS",
  "paid_amount": 125.50, "received_amount": 125.50, "docstatus": 0,
  "references": [{"reference_doctype": "Purchase Invoice", "reference_name": "PINV-1",
                  "allocated_amount": 125.500}]}}"""


class FakeErp:
    def __init__(self, existing=(), draft=DRAFT, insert_status=200):
        self.existing, self.draft, self.insert_status = list(existing), draft, insert_status
        self.inserted: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/api/resource/Payment Entry":
            assert TX in request.url.params["filters"]
            assert "reference_no" in request.url.params["filters"]  # standard, not custom
            rows = [{"name": n} for n in self.existing]
            return httpx.Response(200, json={"data": rows})
        if path.endswith("get_payment_entry"):
            assert b"dt=Purchase+Invoice" in request.content and b"dn=PINV-1" in request.content
            return httpx.Response(200, text=self.draft)
        if request.method == "POST" and path == "/api/resource/Payment Entry":
            if self.insert_status != 200:
                return httpx.Response(self.insert_status, json={
                    "exc_type": "ValidationError", "exception": "Supplier Acme, 555-0101"})
            self.inserted.append(json.loads(request.content, parse_float=Decimal))
            return httpx.Response(200, json={"data": {"name": "ACC-PAY-0001"}})
        raise AssertionError(f"unexpected {request.method} {path}")


def adapter(erp, **kwargs) -> ErpnextAdapter:
    return ErpnextAdapter(BASE, "k", "s", client=httpx.Client(transport=httpx.MockTransport(erp)),
                          **kwargs)


def test_a_payment_is_drafted_by_erpnext_and_submitted_with_the_chain_details():
    erp = FakeErp()
    assert adapter(erp).record_payment(PAYMENT) == "ACC-PAY-0001"
    [doc] = erp.inserted
    assert doc["docstatus"] == 1
    assert doc["reference_no"] == doc["custom_tx_hash"] == TX
    assert doc["custom_payee_wallet"] == WALLET
    assert doc["reference_date"] == doc["posting_date"] == "2026-10-07"
    assert doc["paid_from"] == "Cash - TS"  # ERPNext's default account is kept
    assert doc["paid_amount"] == Decimal("125.50")  # a JSON number with its exact digits


def test_on_testnet_the_entry_is_left_as_a_draft_that_says_so():
    erp = FakeErp()
    assert adapter(erp, draft_payments=True).record_payment(PAYMENT) == "ACC-PAY-0001"
    [doc] = erp.inserted
    assert doc["docstatus"] == 0
    assert doc["custom_remarks"] == 1  # ERPNext keeps the remark instead of writing its own
    assert doc["remarks"].startswith("TESTNET") and "Do not submit" in doc["remarks"]
    assert doc["reference_no"] == TX


def test_the_account_the_money_left_from_can_be_set():
    erp = FakeErp()
    adapter(erp, paid_from="USDC Wallet - TS").record_payment(PAYMENT)
    [doc] = erp.inserted
    assert doc["paid_from"] == "USDC Wallet - TS"
    assert doc["paid_from_account_currency"] is None


def test_fields_the_erp_requires_are_added_to_the_entry():
    erp = FakeErp()
    adapter(erp, payment_extra={"payment_form": "03"}).record_payment(PAYMENT)
    [doc] = erp.inserted
    assert doc["payment_form"] == "03"
    assert doc["reference_no"] == TX


@pytest.mark.parametrize(
    "field", ["paid_amount", "party", "references", "reference_no", "docstatus"]
)
def test_extra_fields_cannot_change_what_was_paid(field):
    with pytest.raises(ValueError, match=field):
        adapter(FakeErp(), payment_extra={field: "x"})


def test_the_same_transaction_is_never_recorded_twice():
    erp = FakeErp(existing=["ACC-PAY-0001"])
    assert adapter(erp).record_payment(PAYMENT) == "ACC-PAY-0001"
    assert erp.inserted == []


@pytest.mark.parametrize("old, new", [
    ('"paid_amount": 125.50', '"paid_amount": 120.00'),
    ('"party": "Acme"', '"party": "Other"'),
    ('"reference_name": "PINV-1"', '"reference_name": "PINV-2"'),
    ('"paid_amount": 125.50', '"paid_amount": null'),
])
def test_nothing_is_written_when_the_draft_does_not_match_the_chain(old, new):
    erp = FakeErp(draft=DRAFT.replace(old, new))
    with pytest.raises(LedgerError, match="record it by hand"):
        adapter(erp).record_payment(PAYMENT)
    assert erp.inserted == []


def test_a_rejected_entry_names_the_error_class_but_not_its_message():
    with pytest.raises(LedgerError) as error:
        adapter(FakeErp(insert_status=417)).record_payment(PAYMENT)
    assert "417" in str(error.value) and "ValidationError" in str(error.value)
    assert "555-0101" not in str(error.value)


def test_amounts_are_sent_as_exact_json_numbers():
    from services.erp.erpnext.client import _dumps_exact

    text = _dumps_exact({"a": Decimal("125.50"), "b": [Decimal("0.000001")], "note": "decimal-x:1"})
    assert text == '{"a": 125.50, "b": [0.000001], "note": "decimal-x:1"}'
