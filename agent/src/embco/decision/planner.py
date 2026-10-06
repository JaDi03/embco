"""Spend the weekly budget on payable invoices, most urgent first."""

from datetime import date
from decimal import Decimal

from embco.decision.models import Action, Decision, Deferral, PaymentPlan


def _urgency(decision: Decision) -> tuple[date, Decimal]:
    return (decision.due_date or date.max, decision.amount)


def plan_payments(decisions: list[Decision], budget: Decimal) -> PaymentPlan:
    payable = sorted((d for d in decisions if d.action is Action.PAY), key=_urgency)
    pay_now: list[Decision] = []
    deferred: list[Deferral] = []
    remaining = budget
    for decision in payable:
        if decision.amount <= remaining:
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
