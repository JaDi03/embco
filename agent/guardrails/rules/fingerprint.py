"""A stable identity for what a decision says, so it can be recognised in later runs."""

import hashlib
import json

from agent.guardrails.controls.base import fmt
from agent.guardrails.rules.models import Decision
from services.fx.rates import PAYABLE_CURRENCY


def fingerprint(decision: Decision) -> str:
    """Same invoice, amount, action and reasons give the same fingerprint in any run.

    The due date is left out on purpose: it changes urgency, not the decision. So is the USDC
    of an invoice in another currency: the day's rate moves it, the invoice stays the same.
    """
    payload = [
        decision.invoice,
        decision.supplier,
        fmt(decision.billed),
        decision.action.value,
        sorted(decision.reasons),
    ]
    if decision.currency != PAYABLE_CURRENCY:
        payload.append(decision.currency)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()
