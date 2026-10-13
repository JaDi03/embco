"""A supplier disabled in the ERP: strong evidence the agent weighs (the shop may have stopped
working with it), not a stop."""

from agent.guardrails.controls.base import Finding, note, passed
from agent.guardrails.controls.context import Context


class SupplierStatus:
    name = "supplier_status"

    def check(self, ctx: Context) -> Finding:
        if ctx.supplier.disabled:
            return note(self.name, "the supplier is disabled in the ERP")
        return passed(self.name, "supplier is active")
