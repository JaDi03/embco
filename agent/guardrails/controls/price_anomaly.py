"""Flag a price well above what this supplier habitually charged for the same item."""

from decimal import Decimal
from statistics import median

from agent.guardrails.controls.base import Finding, ask, fmt, passed
from agent.guardrails.controls.context import Context
from services.erp.models import DocumentLine


class PriceAnomaly:
    name = "price_anomaly"

    def __init__(self, max_increase: Decimal = Decimal("0.15")) -> None:
        self._max_increase = max_increase

    def check(self, ctx: Context) -> Finding:
        flagged = [m for line in ctx.invoice.lines if (m := self._message(line, ctx))]
        if flagged:
            return ask(self.name, "; ".join(flagged))
        return passed(self.name, "prices are in line with past purchases")

    def _message(self, line: DocumentLine, ctx: Context) -> str | None:
        past = [
            earlier.rate
            for invoice in ctx.supplier_invoices
            if invoice.name != ctx.invoice.name and invoice.outstanding_amount == 0
            for earlier in invoice.lines
            if earlier.item_code == line.item_code
        ]
        if not past:
            return None
        habitual = median(past)
        if habitual <= 0 or line.rate <= habitual * (1 + self._max_increase):
            return None
        percent = ((line.rate / habitual) - 1) * 100
        return (
            f"{line.item_code}: rate {fmt(line.rate)} is {fmt(percent.quantize(Decimal(1)))}% "
            f"above the habitual {fmt(habitual)}"
        )
