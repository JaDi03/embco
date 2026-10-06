"""Detect the same supplier invoice entered twice, even if the number is typed differently."""

import re
from datetime import date

from embco.controls.base import Finding, hold, passed
from embco.controls.context import Context
from embco.ledger.models import PurchaseInvoice


def normalize(bill_no: str | None) -> str:
    """Upper case, letters and digits only: 'inv-22 01' and 'INV2201' are the same."""
    return re.sub(r"[^A-Z0-9]", "", (bill_no or "").upper())


def _order(invoice: PurchaseInvoice) -> tuple[date, str]:
    return (invoice.posting_date or date.min, invoice.name)


class DuplicateInvoice:
    name = "duplicate_invoice"

    def check(self, ctx: Context) -> Finding:
        key = normalize(ctx.invoice.bill_no)
        if not key:
            return passed(self.name, "no supplier invoice number to compare")
        mine = _order(ctx.invoice)
        earlier = [
            other.name
            for other in ctx.supplier_invoices
            if other.name != ctx.invoice.name
            and normalize(other.bill_no) == key
            and _order(other) < mine
        ]
        if earlier:
            return hold(self.name, f"same supplier invoice number as {', '.join(earlier)}")
        return passed(self.name, "no earlier invoice with the same number")
