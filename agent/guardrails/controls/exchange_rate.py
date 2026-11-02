"""An invoice in a currency other than the US dollar is paid in USDC at the day's rate.

It holds when there is no rate, when the rate is older than the shop allows (a source that
stopped publishing), or when the invoice is not in the company's own currency, since the books
then need a second conversion the agent does not make.
"""

from agent.guardrails.controls.base import Finding, fmt, hold, passed
from agent.guardrails.controls.context import Context


class ExchangeRateCheck:
    name = "exchange_rate"

    def __init__(self, max_age_days: int) -> None:
        self._max_age = max_age_days

    def check(self, ctx: Context) -> Finding:
        invoice = ctx.invoice
        if invoice.conversion_rate != 1:
            return hold(self.name, f"the invoice is in {invoice.currency}, not the company's own "
                                   "currency; pay it by hand")
        rate = ctx.rate
        if rate is None:
            return hold(self.name, f"no exchange rate for {invoice.currency}: {ctx.rate_problem}")
        age = (ctx.today - rate.on).days
        if age > self._max_age:
            return hold(self.name, f"the {invoice.currency} rate is from {rate.on} ({age} days "
                                   f"old; at most {self._max_age} allowed)")
        return passed(self.name, f"{fmt(invoice.outstanding_amount)} {invoice.currency} is "
                                 f"{fmt(ctx.usdc_amount)} USDC at {fmt(rate.per_usd)} "
                                 f"{invoice.currency} per USD ({rate.source}, {rate.on})")
