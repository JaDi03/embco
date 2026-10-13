"""A payment above the per-payment limit: the shop contract refuses it from the agent, so the
agent knows before trying, and tells the owner what can be done."""

from decimal import Decimal

from agent.guardrails.controls.base import Finding, fmt, note, passed
from agent.guardrails.controls.context import Context


class PaymentLimit:
    name = "payment_limit"

    def __init__(self, max_per_payment: Decimal) -> None:
        self._max = max_per_payment

    def check(self, ctx: Context) -> Finding:
        amount = ctx.invoice.outstanding_amount
        if amount > self._max:
            return note(
                self.name,
                f"amount {fmt(amount)} {ctx.invoice.currency} is above the per-payment "
                f"limit of {fmt(self._max)}",
            )
        return passed(self.name, "within the per-payment limit")
