"""A stable identity for what a decision says, so it can be recognised in later runs."""

import hashlib
import json

from agent.guardrails.controls.base import fmt
from agent.guardrails.rules.models import Decision


def fingerprint(decision: Decision) -> str:
    """Same invoice, amount, action and reasons give the same fingerprint in any run.

    The due date is left out on purpose: it changes urgency, not the decision.
    """
    payload = [
        decision.invoice,
        decision.supplier,
        fmt(decision.amount),
        decision.action.value,
        sorted(decision.reasons),
    ]
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()
