"""Never pay a supplier that has been disabled in the ERP."""

from agent.guardrails.controls.base import Finding, hold, passed
from agent.guardrails.controls.context import Context


class SupplierStatus:
    name = "supplier_status"

    def check(self, ctx: Context) -> Finding:
        if ctx.supplier.disabled:
            return hold(self.name, "the supplier is disabled in the ERP")
        return passed(self.name, "supplier is active")
