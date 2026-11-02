"""What the policy returns. No money moves here: these are decisions with reasons."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from agent.guardrails.controls import Finding
from services.fx.rates import PAYABLE_CURRENCY, ExchangeRate


class Action(StrEnum):
    PAY = "PAY"
    HOLD = "HOLD"
    ASK = "ASK"


@dataclass(frozen=True)
class Decision:
    invoice: str
    supplier: str
    amount: Decimal  # USDC to pay; 0 for an invoice in another currency with no rate
    due_date: date | None
    action: Action
    reasons: tuple[str, ...]
    findings: tuple[Finding, ...]
    # an invoice in another currency: what it says in that currency, and the rate it was
    # converted at (None for USD, where the amount is the invoice's own)
    invoice_amount: Decimal | None = None
    currency: str = PAYABLE_CURRENCY
    rate: ExchangeRate | None = None

    @property
    def billed(self) -> Decimal:
        """The amount due as the invoice states it, in its own currency."""
        return self.amount if self.invoice_amount is None else self.invoice_amount


@dataclass(frozen=True)
class Deferral:
    decision: Decision
    reason: str


@dataclass(frozen=True)
class PaymentPlan:
    """Who gets paid now, who waits for budget, and what needs a person."""

    pay_now: tuple[Decision, ...]
    deferred: tuple[Deferral, ...]
    held: tuple[Decision, ...]
    asked: tuple[Decision, ...]
    budget_left: Decimal
