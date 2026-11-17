"""Spend the weekly budget on payable invoices, most urgent first."""

from datetime import date
from decimal import Decimal

from agent.guardrails.rules.models import Action, Decision, Deferral, PaymentPlan


def _urgency(decision: Decision) -> tuple[date, Decimal]:
    return (decision.due_date or date.max, decision.amount)


def plan_payments(decisions: list[Decision], budget: Decimal,
                  limit: Decimal | None = None) -> PaymentPlan:
    """`limit` is the contract's per-payment limit: a larger payment waits for the owner to
    change it, since the contract would refuse it."""
    payable = sorted((d for d in decisions if d.action is Action.PAY), key=_urgency)
    pay_now: list[Decision] = []
    deferred: list[Deferral] = []
    remaining = budget
    for decision in payable:
        if limit is not None and decision.amount > limit:
            deferred.append(Deferral(decision, f"above the per-payment limit ({limit})"))
        elif decision.amount <= remaining:
            pay_now.append(decision)
            remaining -= decision.amount
        else:
            deferred.append(Deferral(decision, f"not enough weekly budget left ({remaining})"))
    return PaymentPlan(
        pay_now=tuple(pay_now),
        deferred=tuple(deferred),
        held=tuple(d for d in decisions if d.action is Action.HOLD),
        asked=tuple(d for d in decisions if d.action is Action.ASK),
        budget_left=remaining,
    )
