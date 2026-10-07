"""ERP-agnostic domain models. Amounts are Decimal, never float."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Supplier(_Frozen):
    name: str
    wallet_address: str | None = None
    disabled: bool = False


class DocumentLine(_Frozen):
    """One line of a purchase order, receipt or invoice, with its upstream references."""

    item_code: str
    qty: Decimal
    rate: Decimal
    amount: Decimal
    row_id: str | None = None
    purchase_order: str | None = None
    po_detail: str | None = None
    purchase_receipt: str | None = None
    pr_detail: str | None = None


class PaymentRecord(_Frozen):
    """A payment already made to a supplier, with the wallet it was sent to."""

    name: str
    supplier: str
    amount: Decimal
    posting_date: date | None = None
    payee_wallet: str | None = None


class PurchaseOrder(_Frozen):
    name: str
    supplier: str
    transaction_date: date | None = None
    currency: str
    grand_total: Decimal
    status: str | None = None
    lines: tuple[DocumentLine, ...] = ()


class PurchaseReceipt(_Frozen):
    name: str
    supplier: str
    posting_date: date | None = None
    currency: str
    grand_total: Decimal
    lines: tuple[DocumentLine, ...] = ()


class PurchaseInvoice(_Frozen):
    name: str
    supplier: str
    bill_no: str | None = None
    posting_date: date | None = None
    due_date: date | None = None
    currency: str
    grand_total: Decimal
    outstanding_amount: Decimal
    docstatus: int
    lines: tuple[DocumentLine, ...] = ()


class SettledPayment(_Frozen):
    """A payment that is final on chain, to be written into the ledger."""

    invoice: str
    supplier: str
    amount: Decimal
    paid_on: date
    tx_hash: str
    payee_wallet: str
    note: str


class WalletChange(_Frozen):
    """One edit of a supplier's wallet in the ERP, from its change history."""

    old: str | None
    new: str | None
    changed_by: str
    changed_at: datetime


class OwnerMark(_Frozen):
    """The owner's answer written on an invoice in the ERP, and the change that wrote it."""

    verdict: str  # as written in the ERP, e.g. "Approve" or "Reject"
    note: str
    set_by: str
    set_at: datetime
    change_id: str  # the ERP's record of that change, so one mark answers one question
