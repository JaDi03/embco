"""A single payment above the configured limit always needs a person."""

from decimal import Decimal

from agent.guardrails.controls.base import Finding, ask, fmt, passed
from agent.guardrails.controls.context import Context


class PaymentLimit:
    name = "payment_limit"

    def __init__(self, max_per_payment: Decimal) -> None:
        self._max = max_per_payment

    def check(self, ctx: Context) -> Finding:
        amount = ctx.invoice.outstanding_amount
        if amount > self._max:
            return ask(
                self.name,
                f"amount {fmt(amount)} {ctx.invoice.currency} is above the per-payment "
                f"limit of {fmt(self._max)}",
            )
        return passed(self.name, "within the per-payment limit")
