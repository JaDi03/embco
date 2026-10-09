import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from services.erp import ErpnextAdapter, LedgerError

BASE = "https://erp.test"
KEY, SECRET = "k123", "s456"


def make_adapter(handler, **kwargs) -> ErpnextAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ErpnextAdapter(BASE, KEY, SECRET, client=client, **kwargs)


def json_response(body: str, status: int = 200) -> httpx.Response:
    # Raw text on purpose: floats must reach the adapter exactly as the ERP wrote them.
    return httpx.Response(status, text=body, headers={"content-type": "application/json"})


INVOICE = """{"data": {
  "name": "PINV-0001", "supplier": "Acme Drinks", "bill_no": "F-77",
  "posting_date": "2026-10-01", "due_date": "2026-10-15", "currency": "USD",
  "grand_total": 249.995, "outstanding_amount": 6.000000, "docstatus": 1,
  "items": [{"item_code": "COLA-12", "qty": 10.0, "rate": 24.9995, "amount": 249.995,
             "purchase_order": "PO-1", "po_detail": "abc", "purchase_receipt": "PR-1",
             "pr_detail": "def"}]
}}"""


def test_sends_token_auth_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        return json_response(INVOICE)

    make_adapter(handler).get_purchase_invoice("PINV-0001")
    assert seen["auth"] == f"token {KEY}:{SECRET}"


def test_amounts_keep_exact_decimal_precision():
    invoice = make_adapter(lambda r: json_response(INVOICE)).get_purchase_invoice("PINV-0001")
    assert invoice.grand_total == Decimal("249.995")
    assert invoice.outstanding_amount == Decimal("6.000000")
    assert invoice.outstanding_amount.as_tuple().exponent == -6
    assert invoice.due_date == date(2026, 10, 15)


def test_invoice_lines_keep_their_upstream_references():
    invoice = make_adapter(lambda r: json_response(INVOICE)).get_purchase_invoice("PINV-0001")
    line = invoice.lines[0]
    assert (line.purchase_order, line.po_detail) == ("PO-1", "abc")
    assert (line.purchase_receipt, line.pr_detail) == ("PR-1", "def")


def test_supplier_wallet_comes_from_the_custom_field():
    body = '{"data": {"name": "Acme Drinks", "custom_wallet_address": "0xabc", "disabled": 0}}'
    supplier = make_adapter(lambda r: json_response(body)).get_supplier("Acme Drinks")
    assert supplier.wallet_address == "0xabc"
    assert supplier.disabled is False


def test_supplier_wallet_field_name_is_configurable():
    body = '{"data": {"name": "Acme", "custom_usdc": "0xdef"}}'
    adapter = make_adapter(lambda r: json_response(body), wallet_field="custom_usdc")
    assert adapter.get_supplier("Acme").wallet_address == "0xdef"


def test_supplier_without_wallet_has_none():
    body = '{"data": {"name": "Acme", "custom_wallet_address": null}}'
    assert make_adapter(lambda r: json_response(body)).get_supplier("Acme").wallet_address is None


def test_list_unpaid_filters_submitted_with_outstanding_balance():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/resource/Purchase Invoice":
            return json_response('{"data": [{"name": "PINV-0001"}]}')
        return json_response(INVOICE)

    invoices = make_adapter(handler).list_unpaid_purchase_invoices()

    assert [i.name for i in invoices] == ["PINV-0001"]
    filters = json.loads(calls[0].url.params["filters"])
    assert ["docstatus", "=", 1] in filters
    assert ["outstanding_amount", ">", 0] in filters


def test_http_error_raises_ledger_error_without_leaking_credentials():
    adapter = make_adapter(lambda r: json_response('{"exc": "denied"}', status=403))
    with pytest.raises(LedgerError) as excinfo:
        adapter.get_purchase_invoice("PINV-0001")
    assert "403" in str(excinfo.value)
    assert SECRET not in str(excinfo.value)


def test_network_failure_raises_ledger_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(LedgerError):
        make_adapter(handler).get_supplier("Acme")


def test_invalid_json_raises_ledger_error():
    with pytest.raises(LedgerError):
        make_adapter(lambda r: json_response("<html>nope</html>")).get_supplier("Acme")


def test_missing_document_raises_ledger_error():
    with pytest.raises(LedgerError):
        make_adapter(lambda r: json_response('{"message": "x"}')).get_supplier("Acme")
