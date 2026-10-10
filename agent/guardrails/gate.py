"""Can the agent pay this invoice? The answer comes from the checks, never from the model.

The agent may delay or hold a payment the checks allow. It can never pay one they do not allow:
an invoice on HOLD has something wrong, and one on ASK waits for the owner's answer.
"""

from collections.abc import Collection
from datetime import date, timedelta
from decimal import Decimal

from agent.guardrails.controls.base import fmt
from agent.guardrails.rules import Action, Decision

MAX_SCHEDULE_DAYS = 60


def pay_refusal(
    decision: Decision | None,
    *,
    done: Collection[str],
    room: Decimal | None = None,
    committed: Decimal = Decimal(0),
) -> str | None:
    """Why the agent may not pay this invoice now, or None when it may.

    `room` is what the contract still lets the shop pay this week (None when unknown) and
    `committed` what the agent already chose to pay now in this session."""
    if decision is None:
        return "this invoice is not among the unpaid invoices"
    if decision.invoice in done:
        return "the agent already paid or sent this invoice"
    if decision.action is Action.HOLD:
        return "the checks hold this invoice: " + "; ".join(decision.reasons)
    if decision.action is Action.ASK:
        return "the owner must answer first: " + "; ".join(decision.reasons)
    if room is not None and committed + decision.amount > room:
        left = max(room - committed, Decimal(0))
        return (f"only {fmt(left)} USDC left under this week's cap after what you already chose "
                "to pay now; schedule it for later or pay something else first")
    return None


def schedule_refusal(
    decision: Decision | None, pay_on: date, today: date, *, done: Collection[str],
    room: Decimal | None = None, renews_on: date | None = None,
) -> str | None:
    """Why the agent may not schedule this payment for `pay_on`, or None when it may.

    `room` is what the contract still lets the shop pay this week and `renews_on` the shop's
    local day its week renews (None when unknown). A payment that does not fit this week's room
    is never scheduled before the room renews: the date comes from the contract, not a guess."""
    if decision is None:
        return "this invoice is not among the unpaid invoices"
    if decision.invoice in done:
        return "the agent already paid or sent this invoice"
    if decision.action is not Action.PAY:
        return f"the checks do not allow paying it ({decision.action.value}); hold or ask instead"
    if pay_on < today:
        return f"{pay_on} is in the past; today is {today}"
    if pay_on > today + timedelta(days=MAX_SCHEDULE_DAYS):
        return f"schedule at most {MAX_SCHEDULE_DAYS} days ahead; set an alarm to look again"
    if room is not None and decision.amount > room:
        if renews_on is None:
            return (f"only {fmt(room)} USDC is left this week and when the contract's week renews "
                    "cannot be read right now; hold it and look again later")
        if pay_on < renews_on:
            return (f"only {fmt(room)} USDC is left this week; the contract's week renews on "
                    f"{renews_on} (shop time): schedule it on or after that day")
    return None
