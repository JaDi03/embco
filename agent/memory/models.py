"""What the journal keeps, and how its entries are hashed. Entries are never edited."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from agent.guardrails.controls import Finding
from agent.guardrails.rules import Action


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


DOMAIN = "embco-journal-v1"
GENESIS = "0" * 64


def canonical(payload: object) -> str:
    """One byte-exact JSON text per value: sorted keys, no spaces, UTF-8, no floats."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(payload: object) -> str:
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def chain_hash(previous_hash: str, content: dict) -> str:
    """Hash of an entry linked to the one before it, so a silent edit breaks the chain.

    The domain tag keeps these hashes apart from any other hash of the same bytes and lets a
    future format change its rules without ambiguity.
    """
    text = f"{DOMAIN}\n{previous_hash}\n{canonical(content)}"
    return hashlib.sha256(text.encode()).hexdigest()
