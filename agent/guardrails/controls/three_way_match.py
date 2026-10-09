"""Compare what was invoiced with what was ordered and what actually arrived."""

from decimal import Decimal

from agent.guardrails.controls.base import Finding, fmt, hold, passed
from agent.guardrails.controls.context import Context
from services.erp.models import DocumentLine, PurchaseOrder, PurchaseReceipt


def _find(document: PurchaseOrder | PurchaseReceipt | None, row_id: str | None):
    if document is None or row_id is None:
        return None
    return next((line for line in document.lines if line.row_id == row_id), None)


class ThreeWayMatch:
    name = "three_way_match"

    def __init__(self, rate_tolerance: Decimal = Decimal(0)) -> None:
        self._tolerance = rate_tolerance

    def check(self, ctx: Context) -> Finding:
        problems = [p for line in ctx.invoice.lines for p in self._problems(line, ctx)]
        if problems:
            return hold(self.name, "; ".join(problems))
        return passed(self.name, "order, receipt and invoice agree")

    def _problems(self, line: DocumentLine, ctx: Context) -> list[str]:
        item = line.item_code
        if not line.purchase_order or not line.purchase_receipt:
            return [f"{item}: not linked to an order and a receipt"]
        ordered = _find(ctx.orders.get(line.purchase_order), line.po_detail)
        received = _find(ctx.receipts.get(line.purchase_receipt), line.pr_detail)
        if ordered is None or received is None:
            return [f"{item}: the linked order or receipt line was not found"]
        found = []
        if line.qty > received.qty:
            found.append(f"{item}: invoiced {fmt(line.qty)} but received {fmt(received.qty)}")
        if line.qty > ordered.qty:
            found.append(f"{item}: invoiced {fmt(line.qty)} but ordered {fmt(ordered.qty)}")
        if abs(line.rate - ordered.rate) > self._tolerance:
            found.append(
                f"{item}: invoiced rate {fmt(line.rate)} differs from ordered {fmt(ordered.rate)}"
            )
        return found
