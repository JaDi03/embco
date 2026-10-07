"""One full pass of the agent: check its memory, decide, remember, ask for proofs, plan, and
optionally have the AI helper explain what needs the owner.

With `payments`, the invoices planned for now are paid through the shop contract; without one,
the plan is only reported. The owner's approval of a new wallet in the contract also answers
the agent's question about it. Invoices the agent already paid or sent are not planned again.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from embco.circle import CircleError
from embco.controls import WalletChallenge
from embco.decision import (
    Decision,
    DecisionEngine,
    OwnerAnswer,
    PaymentPlan,
    PolicyConfig,
    apply_answers,
    plan_payments,
)
from embco.journal import (
    Change,
    DecisionJournal,
    answers_for,
    issue_wallet_challenges,
    remember,
)
from embco.ledger import LedgerAdapter, LedgerError
from embco.llm import Explainer, Explanation
from embco.payments import ChainError, Payer, PaymentSetupError, Settlement, paid_or_sent
from embco.runner.explain import explain_decisions

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


def run_cycle(
    ledger: LedgerAdapter,
    journal: DecisionJournal,
    policy: PolicyConfig,
    payer: str,
    *,
    at: datetime | None = None,
    explainer: Explainer | None = None,
    payments: Payer | None = None,
) -> CycleReport:
    """Fails closed: a journal that does not verify stops the agent before it decides."""
    now = at or datetime.now(UTC)
    journal.verify()
    raw = DecisionEngine(ledger, policy, proofs=journal).decide_all()
    decisions = apply_answers(raw, answers_for(journal, raw))
    if payments:
        decisions = apply_answers(decisions, _answers_from_chain(payments, decisions, journal))
    memory = remember(journal, decisions, policy, at=now)
    suppliers = [d.supplier for d in decisions]
    challenges = issue_wallet_challenges(journal, ledger, suppliers, payer, at=now)
    notes = {c.decision.invoice: c.note for c in memory.changes}
    explanations = (
        explain_decisions(journal, explainer, decisions, notes, now) if explainer else []
    )
    done = paid_or_sent(journal, decisions) if payments else set()
    plan = plan_payments([d for d in decisions if d.invoice not in done], policy.weekly_budget)
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
    )


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
