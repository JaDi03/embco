"""Exchange rates: how many units of an invoice's currency one US dollar buys, and on which day.

embco asks for the rate itself, so paying an invoice in a local currency works the same with
any ERP. USDC counts as one US dollar, the peg Circle keeps.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

PAYABLE_CURRENCY = "USD"  # the shop contract pays USDC
CENT = Decimal("0.01")  # USD has two decimals (ISO 4217), as the ERP records it


class RateError(Exception):
    """No rate could be had for this currency now; the message says why, for the agent."""


@dataclass(frozen=True)
class ExchangeRate:
    currency: str  # the invoice's currency, e.g. NGN
    per_usd: Decimal  # units of that currency one US dollar buys
    on: date  # the day the source published it for
    source: str


class RateSource(Protocol):
    def usd_rate(self, currency: str) -> ExchangeRate:
        """The latest rate of `currency` against the US dollar; raises RateError."""
        ...


def to_usd(amount: Decimal, rate: ExchangeRate) -> Decimal:
    """An amount in the rate's currency, in US dollars rounded to the cent."""
    return (amount / rate.per_usd).quantize(CENT, rounding=ROUND_HALF_UP)
