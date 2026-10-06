"""Decision journal in a local SQLite file.

Append-only: triggers reject updates and deletes, and every entry is hash-chained to the one
before it, so an edit made around the triggers is caught by verify().
"""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import fields
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from embco.controls import Finding, Outcome
from embco.decision import Action, Decision, PolicyConfig
from embco.journal.base import JournalError
from embco.journal.models import JournalEntry, chain_hash, fingerprint

GENESIS = "0" * 64

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    policy TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs (id),
    invoice TEXT NOT NULL,
    supplier TEXT NOT NULL,
    amount TEXT NOT NULL,
    due_date TEXT,
    action TEXT NOT NULL,
    reasons TEXT NOT NULL,
    findings TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_by_invoice ON decisions (invoice, id);
"""

_APPEND_ONLY = "".join(
    f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
    "BEGIN SELECT RAISE(ABORT, 'the journal is append-only'); END;\n"
    for table in ("runs", "decisions")
    for op in ("UPDATE", "DELETE")
)

_SELECT = """
SELECT d.run_id, r.started_at, r.policy, d.invoice, d.supplier, d.amount, d.due_date,
       d.action, d.reasons, d.findings, d.fingerprint, d.entry_hash
FROM decisions d JOIN runs r ON r.id = d.run_id
"""

_INSERT = """
INSERT INTO decisions (run_id, invoice, supplier, amount, due_date, action, reasons, findings,
                       fingerprint, entry_hash)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_CONTENT_KEYS = (
    "run_id", "recorded_at", "policy", "invoice", "supplier", "amount", "due_date", "action",
    "reasons", "findings", "fingerprint",
)


class SqliteJournal:
    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(path)
        self._db.executescript(_SCHEMA + _APPEND_ONLY)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "SqliteJournal":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record_run(
        self, decisions: Sequence[Decision], policy: PolicyConfig, at: datetime
    ) -> int:
        if at.tzinfo is None:
            raise JournalError("journal timestamps must be timezone-aware")
        started_at = at.isoformat()
        policy_text = _policy_json(policy)
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO runs (started_at, policy) VALUES (?, ?)", (started_at, policy_text)
            )
            run_id = int(cursor.lastrowid or 0)
            previous = self._last_hash()
            for decision in decisions:
                values = (run_id, started_at, policy_text, *_decision_values(decision))
                previous = chain_hash(previous, _content(values))
                self._db.execute(_INSERT, (run_id, *values[3:], previous))
        return run_id

    def last_entry(self, invoice: str) -> JournalEntry | None:
        row = self._db.execute(
            _SELECT + "WHERE d.invoice = ? ORDER BY d.id DESC LIMIT 1", (invoice,)
        ).fetchone()
        return _entry(row) if row else None

    def history(self, invoice: str) -> list[JournalEntry]:
        rows = self._db.execute(_SELECT + "WHERE d.invoice = ? ORDER BY d.id", (invoice,))
        return [_entry(row) for row in rows]

    def verify(self) -> None:
        previous = GENESIS
        for row in self._db.execute(_SELECT + "ORDER BY d.id"):
            expected = chain_hash(previous, _content(row[:-1]))
            if row[-1] != expected:
                raise JournalError(f"journal entry for {row[3]} in run {row[0]} was altered")
            previous = expected

    def _last_hash(self) -> str:
        row = self._db.execute("SELECT entry_hash FROM decisions ORDER BY id DESC LIMIT 1")
        found = row.fetchone()
        return found[0] if found else GENESIS


def _policy_json(policy: PolicyConfig) -> str:
    values = {f.name: str(getattr(policy, f.name)) for f in fields(policy)}
    return json.dumps(values, sort_keys=True)


def _decision_values(decision: Decision) -> tuple:
    findings = [[f.control, f.outcome.value, f.reason] for f in decision.findings]
    return (
        decision.invoice,
        decision.supplier,
        str(decision.amount),
        decision.due_date.isoformat() if decision.due_date else None,
        decision.action.value,
        json.dumps(list(decision.reasons)),
        json.dumps(findings),
        fingerprint(decision),
    )


def _content(values: tuple) -> dict:
    return dict(zip(_CONTENT_KEYS, values, strict=True))


def _entry(row: tuple) -> JournalEntry:
    run_id, started_at, _, invoice, supplier, amount, due, action, reasons, findings, fp, h = row
    return JournalEntry(
        run_id=run_id,
        recorded_at=datetime.fromisoformat(started_at),
        invoice=invoice,
        supplier=supplier,
        amount=Decimal(amount),
        due_date=date.fromisoformat(due) if due else None,
        action=Action(action),
        reasons=tuple(json.loads(reasons)),
        findings=tuple(Finding(c, Outcome(o), r) for c, o, r in json.loads(findings)),
        fingerprint=fp,
        entry_hash=h,
    )
