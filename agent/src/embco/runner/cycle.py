"""One full pass of the agent: check its memory, decide, remember, ask for proofs, plan.

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


@dataclass(frozen=True)
class CycleReport:
    run_id: int
    at: datetime
    decisions: tuple[Decision, ...]
    changes: tuple[Change, ...]
    closed: tuple[str, ...]
    challenges: tuple[WalletChallenge, ...]
    plan: PaymentPlan


def run_cycle(
    ledger: LedgerAdapter,
    journal: DecisionJournal,
    policy: PolicyConfig,
    payer: str,
    *,
    at: datetime | None = None,
) -> CycleReport:
    """Fails closed: a journal that does not verify stops the agent before it decides."""
    now = at or datetime.now(UTC)
    journal.verify()
    raw = DecisionEngine(ledger, policy, proofs=journal).decide_all()
    decisions = apply_answers(raw, answers_for(journal, raw))
    memory = remember(journal, decisions, policy, at=now)
    suppliers = [d.supplier for d in decisions]
    challenges = issue_wallet_challenges(journal, ledger, suppliers, payer, at=now)
    return CycleReport(
        run_id=memory.run_id,
        at=now,
        decisions=tuple(decisions),
        changes=memory.changes,
        closed=memory.closed,
        challenges=tuple(challenges),
        plan=plan_payments(decisions, policy.weekly_budget),
    )
