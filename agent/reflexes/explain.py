"""Ask the AI helper to explain what needs the owner: decisions on HOLD or ASK.

Each decision is explained at most once: the explanation is stored with the decision's
fingerprint and reused, so the model is only called for a decision that is new, changed, or
still unexplained because an earlier call failed. A failing helper is logged and skipped; it
never stops the cycle and never changes a decision.
"""

import logging
from collections.abc import Sequence
from datetime import datetime

from agent.explain import Explainer, ExplainerError, Explanation
from agent.guardrails.rules import Action, Decision, fingerprint
from agent.memory import DecisionJournal

log = logging.getLogger("embco")

EXPLAINED_ACTIONS = (Action.HOLD, Action.ASK)


def explain_decisions(
    journal: DecisionJournal,
    explainer: Explainer,
    decisions: Sequence[Decision],
    notes: dict[str, str],
    at: datetime,
) -> list[Explanation]:
    """Explanations for every HOLD and ASK decision; `notes` says what changed per invoice."""
    explanations = []
    for decision in decisions:
        if decision.action not in EXPLAINED_ACTIONS:
            continue
        key = fingerprint(decision)
        stored = journal.explanation_for(decision.invoice, key)
        if stored:
            explanations.append(stored)
            continue
        try:
            text = explainer.explain(decision, notes.get(decision.invoice, ""))
        except ExplainerError as error:
            log.warning("no explanation for %s: %s", decision.invoice, error)
            continue
        explanation = Explanation(
            invoice=decision.invoice,
            fingerprint=key,
            summary=text.summary.strip(),
            next_step=text.next_step.strip(),
            model=explainer.model,
            created_at=at,
        )
        journal.record_explanation(explanation)
        explanations.append(explanation)
    return explanations
