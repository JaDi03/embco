"""A payment above the per-payment limit: the shop contract refuses it from the agent, so the
agent knows before trying, and tells the owner what can be done."""

from decimal import Decimal

from agent.guardrails.controls.base import Finding, fmt, note, passed
from agent.guardrails.controls.context import Context
from services.fx.rates import PAYABLE_CURRENCY


class PaymentLimit:
    name = "payment_limit"

    def __init__(self, max_per_payment: Decimal) -> None:
        self._max = max_per_payment

    def check(self, ctx: Context) -> Finding:
        amount = ctx.usdc_amount
        if amount is None:
            return passed(self.name, "weighed once there is an exchange rate")
        if amount > self._max:
            invoice = ctx.invoice
            what = f"{fmt(amount)} {invoice.currency}" if invoice.currency == PAYABLE_CURRENCY \
                else f"{fmt(amount)} USDC ({fmt(invoice.outstanding_amount)} {invoice.currency})"
            return note(
                self.name,
                f"amount {what} is above the per-payment limit of {fmt(self._max)}",
            )
        return passed(self.name, "within the per-payment limit")
