"""Turn the agent's decisions into the cycle's payment plan.

The checks have the last word: an invoice on HOLD stays held and one on ASK stays asked, whatever
the agent chose (the agent may only hold it instead). On an invoice the checks allow, the agent's
choice decides: paid now, paid on its date, held, or asked. With no decision from the agent the
invoice waits. Payments due now still go through the weekly budget, most urgent first, and in
observe mode nothing is paid: the agent's choices are only shown.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date

from agent.guardrails.rules import (
    Action,
    Decision,
    Deferral,
    PaymentPlan,
    fingerprint,
    plan_payments,
)
from agent.models import AgentDecision, Autonomy, Choice


def agent_plan(
    decisions: Sequence[Decision],
    agent: Mapping[str, AgentDecision],
    today: date,
    budget,
    autonomy: Autonomy = Autonomy.ACT,
) -> PaymentPlan:
    due: list[Decision] = []
    deferred: list[Deferral] = []
    held: list[Decision] = []
    asked: list[Decision] = []
    for d in decisions:
        mine = agent.get(d.invoice)
        if mine is not None and mine.fingerprint != fingerprint(d):
            mine = None  # made on a decision that has changed since
        if d.action is Action.HOLD:
            held.append(d)
        elif d.action is Action.ASK:
            if mine and mine.choice is Choice.HOLD:
                held.append(_as(d, Action.HOLD, mine))
            else:
                asked.append(d)
        elif mine is None:
            deferred.append(Deferral(d, "waiting for the agent to decide"))
        elif mine.choice is Choice.HOLD:
            held.append(_as(d, Action.HOLD, mine))
        elif mine.choice is Choice.ASK:
            asked.append(_as(d, Action.ASK, mine))
        elif mine.choice is Choice.SCHEDULE and mine.pay_on and mine.pay_on > today:
            deferred.append(Deferral(d, f"the agent scheduled it for {mine.pay_on}: "
                                        f"{mine.reason}"))
        elif autonomy is Autonomy.OBSERVE:
            deferred.append(Deferral(d, f"observe mode: the agent would pay it ({mine.reason})"))
        else:
            due.append(d)
    paying = plan_payments(due, budget)
    return PaymentPlan(
        pay_now=paying.pay_now,
        deferred=(*paying.deferred, *deferred),
        held=tuple(held),
        asked=tuple(asked),
        budget_left=paying.budget_left,
    )


def _as(decision: Decision, action: Action, mine: AgentDecision) -> Decision:
    """The rule decision as the agent left it; its own reason first."""
    return replace(decision, action=action, reasons=(f"agent: {mine.reason}", *decision.reasons))
