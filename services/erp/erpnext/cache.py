"""Read only what changed in the ERP since the last check.

A check used to download every unpaid invoice, every earlier invoice of each supplier, and every
linked order and receipt, one request each. Now, at the start of each check, three light
requests list every invoice, order and receipt with the time it last changed; a document is
downloaded again only when that time changed (or it is new). The amount still due and the due
date are always taken from the fresh list, since the ERP can change them without a new version.

What guards money is never cached: the supplier and its wallet are read live, and the payer reads
the invoice and the supplier again from the ERP itself before every payment. Every few checks the
cache is emptied and everything is read again, as a safety net.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from services.erp.erpnext.adapter import ErpnextAdapter
from services.erp.models import PurchaseInvoice, PurchaseOrder, PurchaseReceipt

FULL_EVERY = 8  # checks between two full reads (every 2 hours at one check per 15 minutes)


class CachedLedger:
    def __init__(self, adapter: ErpnextAdapter, *, full_every: int = FULL_EVERY) -> None:
        self._erp = adapter
        self._full_every = full_every
        self._checks = 0
        self._index: list[dict[str, Any]] | None = None
        self._stamps: dict[str, dict[str, str]] = {}
        self._docs: dict[tuple[str, str], tuple[str, Any]] = {}  # (doctype, name) -> (stamp, doc)
        self.downloads = 0  # documents fetched in full since the last refresh, for the log

    def __getattr__(self, name: str) -> Any:
        return getattr(self._erp, name)  # suppliers, wallets, payments, history: always live

    def refresh(self) -> None:
        """Start of a check: learn what changed. Every few checks, forget everything."""
        if self._checks % self._full_every == 0:
            self._docs.clear()
        self._checks += 1
        self.downloads = 0
        self._index = self._erp.invoice_index()
        self._stamps = {"Purchase Order": self._erp.stamps("Purchase Order"),
                        "Purchase Receipt": self._erp.stamps("Purchase Receipt")}

    def list_unpaid_purchase_invoices(self) -> list[PurchaseInvoice]:
        if self._index is None:
            return self._erp.list_unpaid_purchase_invoices()
        unpaid = [r for r in self._index if _decimal(r.get("outstanding_amount")) > 0]
        unpaid.sort(key=lambda r: (r.get("due_date") or "9999-12-31", r["name"]))
        return [self._invoice(r) for r in unpaid]

    def list_supplier_invoices(self, supplier: str) -> list[PurchaseInvoice]:
        if self._index is None:
            return self._erp.list_supplier_invoices(supplier)
        return [self._invoice(r) for r in self._index if r.get("supplier") == supplier]

    def get_purchase_invoice(self, name: str) -> PurchaseInvoice:
        row = next((r for r in self._index or () if r["name"] == name), None)
        return self._invoice(row) if row else self._erp.get_purchase_invoice(name)

    def get_purchase_order(self, name: str) -> PurchaseOrder:
        return self._linked("Purchase Order", name, self._erp.get_purchase_order)

    def get_purchase_receipt(self, name: str) -> PurchaseReceipt:
        return self._linked("Purchase Receipt", name, self._erp.get_purchase_receipt)

    def _invoice(self, row: dict[str, Any]) -> PurchaseInvoice:
        doc = self._cached("Purchase Invoice", row["name"], str(row.get("modified")),
                           self._erp.get_purchase_invoice)
        due = row.get("due_date")
        return doc.model_copy(update={
            "outstanding_amount": _decimal(row.get("outstanding_amount")),
            "due_date": date.fromisoformat(str(due)[:10]) if due else None,
        })

    def _linked(self, doctype: str, name: str, fetch) -> Any:
        stamp = self._stamps.get(doctype, {}).get(name)
        if stamp is None:  # not listed (a draft, or beyond the list): read it live
            self.downloads += 1
            return fetch(name)
        return self._cached(doctype, name, stamp, fetch)

    def _cached(self, doctype: str, name: str, stamp: str, fetch) -> Any:
        kept = self._docs.get((doctype, name))
        if kept and kept[0] == stamp:
            return kept[1]
        doc = fetch(name)
        self.downloads += 1
        self._docs[(doctype, name)] = (stamp, doc)
        return doc


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else Decimal(0)
