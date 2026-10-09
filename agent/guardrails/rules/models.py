"""What the policy returns. No money moves here: these are decisions with reasons."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from agent.guardrails.controls import Finding


class Action(StrEnum):
    PAY = "PAY"
    HOLD = "HOLD"
    ASK = "ASK"


@dataclass(frozen=True)
class Decision:
    invoice: str
    supplier: str
    amount: Decimal
    due_date: date | None
    action: Action
    reasons: tuple[str, ...]
    findings: tuple[Finding, ...]


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
