import json
from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest

from embco.controls import PayeeWallet
from embco.ledger import ErpnextAdapter
from embco.ledger.models import SettledPayment, Supplier
from support import WALLET_A, WALLET_B, FakeLedger, context_for

BANK = "USDC on Arc"
TX = "0x" + "e7" * 32
DRAFT = {"doctype": "Payment Entry", "payment_type": "Pay", "party": "Acme",
         "paid_amount": 125.5, "received_amount": 125.5, "docstatus": 0,
         "references": [{"reference_doctype": "Purchase Invoice", "reference_name": "PINV-1",
                         "allocated_amount": 125.5}]}


def account(name, wallet, owner="clerk@shop.test", creation="2026-10-01 08:00:00"):
    return {"name": name, "bank_account_no": wallet, "owner": owner, "creation": creation}


class FakeErp:
    """ERPNext with no custom fields: wallets live in Bank Account rows of one bank."""

    def __init__(self, accounts=(), versions=None, payments=()):
        self.accounts, self.versions, self.payments = list(accounts), versions or {}, payments
        self.inserted: list[dict] = []
        self.account_filters: list = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        if path == "/api/resource/Supplier/Acme":
            return httpx.Response(200, json={"data": {"name": "Acme", "disabled": 0}})
        if path == "/api/resource/Bank Account":
            self.account_filters.append(json.loads(params["filters"]))
            return httpx.Response(200, json={"data": self.accounts})
        if path.endswith("get_docinfo"):
            return httpx.Response(200, json={"docinfo": {
                "versions": self.versions.get(params["name"], [])}})
        if request.method == "GET" and path == "/api/resource/Payment Entry":
            assert "custom_payee_wallet" not in params["fields"]  # no custom field is asked for
            return httpx.Response(200, json={"data": list(self.payments)})
        if path.endswith("get_payment_entry"):
            return httpx.Response(200, json={"message": DRAFT})
        if request.method == "POST" and path == "/api/resource/Payment Entry":
            self.inserted.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"name": "ACC-PAY-0001"}})
        raise AssertionError(f"unexpected {request.method} {path}")


def adapter(erp) -> ErpnextAdapter:
    return ErpnextAdapter("https://erp.test", "k", "s", wallet_bank=BANK,
                          client=httpx.Client(transport=httpx.MockTransport(erp)))


def test_the_wallet_is_the_number_of_the_suppliers_account_at_the_usdc_bank():
    erp = FakeErp(accounts=[account("Acme USDC - USDC on Arc", WALLET_A)])
    supplier = adapter(erp).get_supplier("Acme")
    assert supplier.wallet_address == WALLET_A and supplier.wallet_problem is None
    [filters] = erp.account_filters
    assert ["party", "=", "Acme"] in filters and ["bank", "=", BANK] in filters
    assert ["disabled", "=", 0] in filters  # a disabled account is not a wallet on file


def test_no_account_at_the_usdc_bank_means_no_wallet():
    assert adapter(FakeErp()).get_supplier("Acme").wallet_address is None


def test_two_different_wallets_are_not_guessed_between():
    erp = FakeErp(accounts=[account("A1", WALLET_A), account("A2", WALLET_B)])
    supplier = adapter(erp).get_supplier("Acme")
    assert supplier.wallet_address is None
    assert "2 active accounts" in supplier.wallet_problem


def test_the_same_wallet_twice_is_one_wallet():
    erp = FakeErp(accounts=[account("A1", WALLET_A), account("A2", "0x" + WALLET_A[2:].upper())])
    assert adapter(erp).get_supplier("Acme").wallet_address == WALLET_A


def test_a_wallet_problem_holds_the_payment():
    ledger = FakeLedger()
    ledger.supplier = Supplier(name="S", wallet_problem="2 active accounts at USDC on Arc")
    finding = PayeeWallet().check(context_for(ledger))
    assert finding.outcome.name == "HOLD" and "2 active accounts" in finding.reason


def test_who_set_the_wallet_comes_from_the_account_history():
    edit = json.dumps({"changed": [["bank_account_no", WALLET_A, WALLET_B]]})
    versions = {"A1": [{"owner": "clerk@shop.test", "creation": "2026-10-07 09:30:00",
                        "data": edit}]}
    erp = FakeErp(accounts=[account("A1", WALLET_B, owner="owner@shop.test")], versions=versions)
    [edit, created] = adapter(erp).wallet_changes("Acme")
    assert (edit.new, edit.changed_by, edit.changed_at) == (WALLET_B, "clerk@shop.test",
                                                            datetime(2026, 10, 7, 9, 30))
    assert (created.old, created.new, created.changed_by) == (None, WALLET_A, "owner@shop.test")


def test_a_wallet_never_edited_was_set_by_whoever_created_the_account():
    erp = FakeErp(accounts=[account("A1", WALLET_A, owner="clerk@shop.test")])
    [created] = adapter(erp).wallet_changes("Acme")
    assert (created.new, created.changed_by, created.changed_at) == (
        WALLET_A, "clerk@shop.test", datetime(2026, 10, 1, 8, 0))


def test_the_paid_wallet_is_kept_in_the_entry_remarks_and_read_back():
    erp = FakeErp(accounts=[account("Acme USDC - USDC on Arc", WALLET_A)])
    erp_adapter = adapter(erp)
    erp_adapter.record_payment(SettledPayment(
        invoice="PINV-1", supplier="Acme", amount=Decimal("125.5"), paid_on=date(2026, 10, 7),
        tx_hash=TX, payee_wallet=WALLET_A, note="PAY decided by the embco agent"))
    [doc] = erp.inserted
    assert doc["custom_remarks"] == 1  # without it ERPNext writes its own remarks
    assert doc["party_bank_account"] == "Acme USDC - USDC on Arc"
    erp.payments = [{"name": "ACC-PAY-0001", "party": "Acme", "posting_date": "2026-10-07",
                     "paid_amount": 125.5, "remarks": doc["remarks"]}]
    [paid] = erp_adapter.list_payments("Acme")
    assert paid.payee_wallet == WALLET_A


@pytest.mark.parametrize("remarks", [None, "", "Amount USD 10 paid to Acme"])
def test_a_payment_made_by_hand_has_no_known_wallet(remarks):
    erp = FakeErp(payments=[{"name": "P1", "party": "Acme", "posting_date": "2026-10-07",
                             "paid_amount": 10, "remarks": remarks}])
    [paid] = adapter(erp).list_payments("Acme")
    assert paid.payee_wallet is None


def test_on_testnet_the_agents_own_draft_counts_as_the_last_payment():
    erp = FakeErp(accounts=[account("Acme USDC - USDC on Arc", WALLET_A)])
    testnet = ErpnextAdapter("https://erp.test", "k", "s", wallet_bank=BANK, draft_payments=True,
                             client=httpx.Client(transport=httpx.MockTransport(erp)))
    testnet.record_payment(SettledPayment(
        invoice="PINV-1", supplier="Acme", amount=Decimal("125.5"), paid_on=date(2026, 10, 7),
        tx_hash=TX, payee_wallet=WALLET_A, note="PAY decided by the embco agent"))
    [doc] = erp.inserted
    erp.payments = [
        {"name": "ACC-PAY-0001", "party": "Acme", "posting_date": "2026-10-07",
         "paid_amount": 125.5, "docstatus": 0, "remarks": doc["remarks"]},
        {"name": "ACC-PAY-0002", "party": "Acme", "posting_date": "2026-10-08",
         "paid_amount": 9, "docstatus": 0, "remarks": f"to wallet {WALLET_B}, typed by hand"},
    ]
    [paid] = testnet.list_payments("Acme")  # a draft someone else left does not count
    assert (paid.name, paid.payee_wallet) == ("ACC-PAY-0001", WALLET_A)
    assert adapter(erp).list_payments("Acme") == []  # off testnet only submitted entries count
