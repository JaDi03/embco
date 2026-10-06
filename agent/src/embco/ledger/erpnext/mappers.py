"""Translate raw ERPNext documents into ERP-agnostic domain models."""

from datetime import date
from decimal import Decimal
from typing import Any

from embco.ledger.models import (
    DocumentLine,
    PaymentRecord,
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseReceipt,
    Supplier,
)


def to_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def to_date(value: Any) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def to_line(row: dict[str, Any]) -> DocumentLine:
    return DocumentLine(
        item_code=row.get("item_code") or "",
        qty=to_decimal(row.get("qty")),
        rate=to_decimal(row.get("rate")),
        amount=to_decimal(row.get("amount")),
        row_id=row.get("name") or None,
        purchase_order=row.get("purchase_order") or None,
        po_detail=row.get("po_detail") or None,
        purchase_receipt=row.get("purchase_receipt") or None,
        pr_detail=row.get("pr_detail") or None,
    )


def _lines(row: dict[str, Any]) -> tuple[DocumentLine, ...]:
    return tuple(to_line(r) for r in row.get("items", []))


def to_supplier(row: dict[str, Any], wallet_field: str) -> Supplier:
    return Supplier(
        name=row["name"],
        wallet_address=row.get(wallet_field) or None,
        disabled=bool(row.get("disabled")),
    )


def to_payment(row: dict[str, Any], wallet_field: str) -> PaymentRecord:
    return PaymentRecord(
        name=row["name"],
        supplier=row["party"],
        amount=to_decimal(row.get("paid_amount")),
        posting_date=to_date(row.get("posting_date")),
        payee_wallet=row.get(wallet_field) or None,
    )


def to_purchase_order(row: dict[str, Any]) -> PurchaseOrder:
    return PurchaseOrder(
        name=row["name"],
        supplier=row["supplier"],
        transaction_date=to_date(row.get("transaction_date")),
        currency=row.get("currency") or "",
        grand_total=to_decimal(row.get("grand_total")),
        status=row.get("status"),
        lines=_lines(row),
    )


def to_purchase_receipt(row: dict[str, Any]) -> PurchaseReceipt:
    return PurchaseReceipt(
        name=row["name"],
        supplier=row["supplier"],
        posting_date=to_date(row.get("posting_date")),
        currency=row.get("currency") or "",
        grand_total=to_decimal(row.get("grand_total")),
        lines=_lines(row),
    )


def to_purchase_invoice(row: dict[str, Any]) -> PurchaseInvoice:
    return PurchaseInvoice(
        name=row["name"],
        supplier=row["supplier"],
        bill_no=row.get("bill_no") or None,
        posting_date=to_date(row.get("posting_date")),
        due_date=to_date(row.get("due_date")),
        currency=row.get("currency") or "",
        grand_total=to_decimal(row.get("grand_total")),
        outstanding_amount=to_decimal(row.get("outstanding_amount")),
        docstatus=int(row.get("docstatus", 0)),
        lines=_lines(row),
    )
