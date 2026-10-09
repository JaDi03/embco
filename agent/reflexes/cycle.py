"""One full pass of the agent: check its memory, decide, remember, ask for proofs, plan, and
optionally have the AI helper explain what needs the owner.

With a `brain`, Claude decides which of the invoices the rules allow are paid and when; without
one nothing is planned for payment. With `payments`, what is planned for now is paid through the
shop contract; without one, the plan is only reported. The owner's approval of a new wallet in
the contract also answers the agent's question about it, and so does an owner's mark on the
invoice in the ERP. Invoices the agent already paid or sent are not planned again.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

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
    done = paid_or_sent(journal, decisions) if payments else set()
    unpaid = [d for d in decisions if d.invoice not in done]
    thought = None
    if brain is None:
        plan = _without_brain(plan_payments(unpaid, policy.weekly_budget))
    else:
        thought = think(brain, journal=journal, ledger=ledger, decisions=decisions,
                        changes=memory.changes, policy=policy, done=done, now=now, extra=wakes)
        plan = agent_plan(unpaid, thought.agent, now.astimezone(brain.zone).date(),
                          policy.weekly_budget, brain.autonomy)
    settlement = _settle(payments, plan, journal) if payments else None
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


def _settle(payer: Payer, plan: PaymentPlan, journal: DecisionJournal) -> Settlement:
    """A payment problem never stops the agent from deciding; it is reported instead."""
    try:
        return payer.settle(plan, journal)
    except (PaymentSetupError, ChainError, CircleError) as error:
        log.warning("payments skipped this cycle: %s", error)
        return Settlement(problem=str(error))
