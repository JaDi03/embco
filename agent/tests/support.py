"""Builders and an in-memory ledger so controls and decisions are tested without an ERP."""

from datetime import date
from decimal import Decimal

from embco.controls import Context, ContextBuilder
from embco.ledger.models import (
    DocumentLine,
    PaymentRecord,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseReceipt,
    Supplier,
)

WALLET_A = "0x" + "a1" * 20
WALLET_B = "0x" + "b2" * 20


def _line(row_id, qty, rate, **links) -> DocumentLine:
    return DocumentLine(
        item_code="COLA", qty=Decimal(qty), rate=Decimal(rate), amount=Decimal(qty) * Decimal(rate),
        row_id=row_id, **links,
    )


def make_invoice(name="PINV-1", bill_no="B-1", qty="10", rate="100", outstanding=None, **kw):
    line = _line("in1", qty, rate, purchase_order="PO-1", po_detail="po1",
                 purchase_receipt="PR-1", pr_detail="pr1")
    total = Decimal(qty) * Decimal(rate)
    fields = {
        "name": name, "supplier": "S", "bill_no": bill_no, "posting_date": date(2026, 10, 1),
        "due_date": date(2026, 10, 10), "currency": "NGN", "grand_total": total,
        "outstanding_amount": total if outstanding is None else Decimal(outstanding),
        "docstatus": 1, "lines": (line,),
    }
    return PurchaseInvoice(**{**fields, **kw})


class FakeLedger:
    def __init__(self) -> None:
        self.supplier = Supplier(name="S", wallet_address=WALLET_A)
        self.orders = {"PO-1": PurchaseOrder(
            name="PO-1", supplier="S", currency="NGN", grand_total=Decimal(1000),
            lines=(_line("po1", "10", "100"),))}
        self.receipts = {"PR-1": PurchaseReceipt(
            name="PR-1", supplier="S", currency="NGN", grand_total=Decimal(1000),
            lines=(_line("pr1", "10", "100"),))}
        self.pending = [make_invoice()]
        self.history = [make_invoice("PINV-0", "H-0", outstanding="0",
                                     posting_date=date(2026, 9, 1))]
        self.payments = [PaymentRecord(name="PAY-0", supplier="S", amount=Decimal(1000),
                                       payee_wallet=WALLET_A)]

    def get_supplier(self, name: str) -> Supplier:
        return self.supplier

    def get_purchase_order(self, name: str) -> PurchaseOrder:
        return self.orders[name]

    def get_purchase_receipt(self, name: str) -> PurchaseReceipt:
        return self.receipts[name]

    def get_purchase_invoice(self, name: str) -> PurchaseInvoice:
        return next(i for i in [*self.history, *self.pending] if i.name == name)

    def list_unpaid_purchase_invoices(self) -> list[PurchaseInvoice]:
        return list(self.pending)

    def list_supplier_invoices(self, supplier: str) -> list[PurchaseInvoice]:
        return [*self.history, *self.pending]

    def list_payments(self, supplier: str) -> list[PaymentRecord]:
        return list(self.payments)


def context_for(ledger: FakeLedger, invoice: PurchaseInvoice | None = None) -> Context:
    return ContextBuilder(ledger).build(invoice or ledger.pending[0])
