"""One full pass of the agent: check its memory, decide, remember, ask for proofs, plan, and
optionally have the AI helper explain what needs the owner.

Nothing is paid here. The plan is what would be paid once payments exist.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from embco.controls import WalletChallenge
from embco.decision import (
    Decision,
    DecisionEngine,
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
from embco.ledger import LedgerAdapter
from embco.llm import Explainer, Explanation
from embco.runner.explain import explain_decisions


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


def run_cycle(
    ledger: LedgerAdapter,
    journal: DecisionJournal,
    policy: PolicyConfig,
    payer: str,
    *,
    at: datetime | None = None,
    explainer: Explainer | None = None,
) -> CycleReport:
    """Fails closed: a journal that does not verify stops the agent before it decides."""
    now = at or datetime.now(UTC)
    journal.verify()
    raw = DecisionEngine(ledger, policy, proofs=journal).decide_all()
    decisions = apply_answers(raw, answers_for(journal, raw))
    memory = remember(journal, decisions, policy, at=now)
    suppliers = [d.supplier for d in decisions]
    challenges = issue_wallet_challenges(journal, ledger, suppliers, payer, at=now)
    notes = {c.decision.invoice: c.note for c in memory.changes}
    explanations = (
        explain_decisions(journal, explainer, decisions, notes, now) if explainer else []
    )
    return CycleReport(
        run_id=memory.run_id,
        at=now,
        decisions=tuple(decisions),
        changes=memory.changes,
        closed=memory.closed,
        challenges=tuple(challenges),
        plan=plan_payments(decisions, policy.weekly_budget),
        explanations=tuple(explanations),
    )
