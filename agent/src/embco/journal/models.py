"""What the journal keeps: one entry per decision, never edited afterwards."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from embco.controls import Finding
from embco.controls.base import fmt
from embco.decision import Action, Decision


@dataclass(frozen=True)
class JournalEntry:
    run_id: int
    recorded_at: datetime
    invoice: str
    supplier: str
    amount: Decimal
    due_date: date | None
    action: Action
    reasons: tuple[str, ...]
    findings: tuple[Finding, ...]
    fingerprint: str
    entry_hash: str


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
    return _sha256(payload)


def chain_hash(previous_hash: str, content: dict) -> str:
    """Hash of an entry linked to the one before it, so a silent edit breaks the chain."""
    return _sha256([previous_hash, content])


def _sha256(payload: object) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()
