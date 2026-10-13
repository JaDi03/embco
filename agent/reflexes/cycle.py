"""One full pass: the checks, the agent's memory, proofs, and the agent's session.

With a `brain`, Claude decides what to do with each invoice the checks allow, and pays during
its session: with `payments`, its pay_now sends the payment through the shop contract and tells
it what happened, so it can act on the result. Without a brain nothing is paid. Before the
session, payments already sent are followed to their final state and written in the ERP; that
needs no decision. The owner's approval of a new wallet in the contract also answers the
agent's question about it, and so does an owner's mark on the invoice in the ERP.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from agent.explain import Explainer, Explanation
from agent.guardrails.controls import WalletChallenge
from agent.guardrails.rules import (
    Decision,
    DecisionEngine,
    Deferral,
    OwnerAnswer,
    PaymentPlan,
    PolicyConfig,
    apply_answers,
    plan_payments,
)
from agent.memory import (
    Change,
    DecisionJournal,
    answers_for,
    answers_from_erp,
    issue_wallet_challenges,
    remember,
)
from agent.models import Wake
from agent.plan import agent_plan
from agent.reflexes.explain import explain_decisions
from agent.think import BrainSetup, Thought, think
from services.circle import CircleError
from services.erp import LedgerAdapter, LedgerError
from services.payments import ChainError, Payer, PaymentSetupError, Settlement, paid_or_sent

log = logging.getLogger("embco")


@dataclass(frozen=True)
class CycleReport:
    run_id: int
    at: datetime
    decisions: tuple[Decision, ...]
    changes: tuple[Change, ...]
    closed: tuple[str, ...]
    challenges: tuple[WalletChallenge, ...]
    plan: PaymentPlan
    explanations: tuple[Explanation, ...] = ()
    settlement: Settlement | None = None
    already_paid: tuple[str, ...] = ()
    thought: Thought | None = None  # what the agent did this cycle, when it has a brain


def run_cycle(
    ledger: LedgerAdapter,
    journal: DecisionJournal,
    policy: PolicyConfig,
    payer: str,
    *,
    at: datetime | None = None,
    explainer: Explainer | None = None,
    payments: Payer | None = None,
    owners: tuple[str, ...] = (),
    brain: BrainSetup | None = None,
    wakes: Sequence[Wake] = (),
    messages: Sequence[str] = (),
    conversation: Sequence[Mapping[str, Any]] = (),
) -> CycleReport:
    """Fails closed: a journal that does not verify stops the agent before it decides.

    With a `brain`, the rules decide what may be paid and the agent decides what is paid and
    when; `wakes` are reasons to wake it found outside the cycle (owner answers, signatures)."""
    now = at or datetime.now(UTC)
    journal.verify()
    raw = DecisionEngine(ledger, policy, proofs=journal).decide_all()
    decisions = apply_answers(raw, answers_for(journal, raw))
    if payments:
        decisions = apply_answers(decisions, _answers_from_chain(payments, decisions, journal))
    if owners:
        decisions = apply_answers(
            decisions, answers_from_erp(journal, ledger, decisions, owners, now)
        )
    memory = remember(journal, decisions, policy, at=now)
    suppliers = [d.supplier for d in decisions]
    challenges = issue_wallet_challenges(journal, ledger, suppliers, payer, at=now)
    notes = {c.decision.invoice: c.note for c in memory.changes}
    explanations = (
        explain_decisions(journal, explainer, decisions, notes, now) if explainer else []
    )
    settled = _reconcile(payments, decisions, journal) if payments else None
    done = paid_or_sent(journal, decisions) if payments else set()
    thought = None
    if brain is None:
        unpaid = [d for d in decisions if d.invoice not in done]
        plan = _without_brain(plan_payments(unpaid, policy.weekly_budget))
    else:
        thought = think(brain, journal=journal, ledger=ledger, decisions=decisions,
                        changes=memory.changes, policy=policy, done=done, now=now, extra=wakes,
                        messages=messages, conversation=conversation, payer=payments)
        done = paid_or_sent(journal, decisions) if payments else set()
        unpaid = [d for d in decisions if d.invoice not in done]
        plan = agent_plan(unpaid, thought.agent, now.astimezone(brain.zone).date(),
                          policy.weekly_budget, brain.autonomy)
    settlement = None
    if settled is not None:
        made = thought.payments if thought else ()
        settlement = replace(settled, events=(*settled.events, *made))
    return CycleReport(
        run_id=memory.run_id,
        at=now,
        decisions=tuple(decisions),
        changes=memory.changes,
        closed=memory.closed,
        challenges=tuple(challenges),
        plan=plan,
        explanations=tuple(explanations),
        settlement=settlement,
        already_paid=tuple(sorted(done)),
        thought=thought,
    )


def _without_brain(plan: PaymentPlan) -> PaymentPlan:
    """No payment without the agent's decision: with the brain off, the rules only report."""
    waiting = tuple(Deferral(d, "the agent's brain is off; nothing is paid without its decision")
                    for d in plan.pay_now)
    return replace(plan, pay_now=(), deferred=(*waiting, *plan.deferred))


def _answers_from_chain(
    payer: Payer, decisions: list[Decision], journal: DecisionJournal
) -> dict[str, OwnerAnswer]:
    """The owner's wallet approvals in the contract, saved as answers like any other."""
    try:
        answers = payer.answers_from_chain(decisions, journal)
    except (ChainError, LedgerError) as error:
        log.warning("wallet approvals not checked this cycle: %s", error)
        return {}
    for answer in answers:
        journal.record_answer(answer)
    return {a.invoice: a for a in answers}


def _reconcile(payer: Payer, decisions: list[Decision], journal: DecisionJournal) -> Settlement:
    """Payments already sent are followed and booked before the agent looks, so it sees where
    they stand. A problem here never stops the agent from deciding; it is reported instead."""
    try:
        return payer.reconcile(decisions, journal)
    except (PaymentSetupError, ChainError, CircleError) as error:
        log.warning("payments not followed this cycle: %s", error)
        return Settlement(problem=str(error))
