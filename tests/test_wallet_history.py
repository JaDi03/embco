import json
from datetime import datetime

import httpx

from agent.guardrails.controls import PayeeWallet
from services.erp import ErpnextAdapter
from services.erp.models import Supplier, WalletChange
from support import WALLET_A, WALLET_B, FakeLedger, context_for

CHANGE = WalletChange(old=WALLET_A, new=WALLET_B, changed_by="clerk@shop.test",
                      changed_at=datetime(2026, 10, 7, 9, 30))


def test_the_reason_says_who_set_the_new_wallet_and_when():
    ledger = FakeLedger()
    ledger.supplier = Supplier(name="S", wallet_address=WALLET_B)
    ledger.changes = [CHANGE]
    finding = PayeeWallet().check(context_for(ledger))
    assert "set in the ERP by clerk@shop.test on 2026-10-07 09:30, ERP time" in finding.reason


def test_the_history_is_not_read_when_the_wallet_did_not_change():
    ledger = FakeLedger()  # wallet on file is the one paid last time
    calls = []
    ledger.wallet_changes = lambda supplier: calls.append(supplier) or []
    context_for(ledger)
    assert calls == []


def test_the_adapter_reads_wallet_edits_from_the_document_history():
    versions = [
        {"owner": "clerk@shop.test", "creation": "2026-10-07 09:30:00.123",
         "data": json.dumps({"changed": [["custom_wallet_address", WALLET_A, WALLET_B],
                                         ["supplier_group", "Local", "Distributor"]]})},
        {"owner": "owner@shop.test", "creation": "2026-10-01 08:00:00",
         "data": json.dumps({"changed": [["custom_wallet_address", None, WALLET_A]]})},
        {"owner": "x", "creation": "bad date", "data": "{}"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("frappe.desk.form.load.get_docinfo")
        assert request.url.params["doctype"] == "Supplier"
        return httpx.Response(200, json={"docinfo": {"versions": versions}})

    adapter = ErpnextAdapter("https://erp.test", "k", "s",
                             client=httpx.Client(transport=httpx.MockTransport(handler)))
    changes = adapter.wallet_changes("S")
    assert [(c.new, c.changed_by) for c in changes] == [(WALLET_B, "clerk@shop.test"),
                                                        (WALLET_A, "owner@shop.test")]
    assert changes[1].old is None
