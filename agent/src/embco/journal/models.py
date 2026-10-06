"""What the journal keeps: one entry per decision, never edited afterwards."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from embco.controls import Finding
from embco.decision import Action


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


def chain_hash(previous_hash: str, content: dict) -> str:
    """Hash of an entry linked to the one before it, so a silent edit breaks the chain."""
    text = json.dumps([previous_hash, content], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()
