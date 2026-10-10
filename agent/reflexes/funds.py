"""Wake the agent when the money will not reach: what is committed for the coming days against
what can actually be paid (the owner's balance, or the authorization if lower).

Committed: what the agent chose to pay now or scheduled, and invoices the checks allow that fall
due without a decision yet, within the next 7 days. The first day the running total goes past
what is available is the day the owner has to act, so that is what the agent is told. It fires
once per day and per figure: a new shortfall wakes it again, the same one does not.
"""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from agent.guardrails.controls.base import fmt
from agent.guardrails.rules import Action, Decision, fingerprint
from agent.models import AgentDecision, Choice, Wake

HORIZON_DAYS = 7


def commitments(decisions: Sequence[Decision], standing: Mapping[str, AgentDecision],
                done: set[str], today: date) -> list[tuple[date, Decimal, str]]:
    """(day, amount, invoice) for every payment expected within the horizon, by day."""
    until = today + timedelta(days=HORIZON_DAYS)
    out = []
    for d in decisions:
        if d.invoice in done or d.action is not Action.PAY:
            continue
        mine = standing.get(d.invoice)
        if mine is not None and mine.fingerprint != fingerprint(d):
            mine = None
        if mine is None:
            day = d.due_date or today
        elif mine.choice is Choice.PAY_NOW:
            day = today
        elif mine.choice is Choice.SCHEDULE and mine.pay_on:
            day = mine.pay_on
        else:
            continue  # held or asked: not expected to go out
        if day <= until:
            out.append((max(day, today), d.amount, d.invoice))
    return sorted(out)


def funds_wake(decisions: Sequence[Decision], standing: Mapping[str, AgentDecision],
               done: set[str], funds: Mapping[str, str] | None, today: date,
               fired: set[str]) -> Wake | None:
    if not funds:
        return None
    try:
        available = Decimal(funds["available_to_pay"])
    except (KeyError, InvalidOperation):
        return None
    running = Decimal(0)
    names = []
    for day, amount, invoice in commitments(decisions, standing, done, today):
        running += amount
        names.append(invoice)
        if running > available:
            key = f"funds:{today}:{fmt(running)}:{fmt(available)}"
            if key in fired:
                return None
            return Wake("funds", (
                f"Money will not reach: by {day} the payments expected ({', '.join(names)}) add "
                f"up to {fmt(running)} USDC, but only {fmt(available)} USDC can be paid (owner's "
                f"balance {funds.get('owner_balance')}, payments authorized: "
                f"{funds.get('payments_authorized')}). Tell the owner with notify_owner."),
                key=key)
    return None
