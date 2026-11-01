"""What the ERP says about paying this invoice and this supplier.

A hold put in the ERP, on the invoice or on payments to the supplier, stops the payment until it
is lifted or its release date passes: ERPNext itself refuses a payment entry for an invoice on
hold. A supplier disabled in the ERP is strong evidence the agent weighs (the shop may have
stopped working with it), not a stop.
"""

from datetime import date

from agent.guardrails.controls.base import Finding, hold, note, passed
from agent.guardrails.controls.context import Context


def until(release: date | None) -> str:
    return f" until {release}" if release else ""


class SupplierStatus:
    name = "supplier_status"

    def check(self, ctx: Context) -> Finding:
        if ctx.invoice.held(ctx.today):
            return hold(self.name, "the invoice is on hold in the ERP"
                        + until(ctx.invoice.on_hold_until))
        if ctx.supplier.payments_held(ctx.today):
            return hold(self.name, "payments to the supplier are on hold in the ERP"
                        + until(ctx.supplier.on_hold_until))
        if ctx.supplier.disabled:
            return note(self.name, "the supplier is disabled in the ERP")
        return passed(self.name, "supplier is active")
