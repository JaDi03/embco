"""Decision journal in a local SQLite file.

Append-only: triggers reject updates and deletes, and decisions and owner answers are each
hash-chained to the entry before them, so an edit made around the triggers is caught by verify().
"""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import fields
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from embco.controls import Finding, Outcome
from embco.decision import Action, Decision, OwnerAnswer, PolicyConfig, Verdict, fingerprint
from embco.journal import schema
from embco.journal.base import JournalError
from embco.journal.models import JournalEntry, chain_hash

GENESIS = "0" * 64


class SqliteJournal:
    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(path)
        self._db.executescript(schema.TABLES + schema.APPEND_ONLY)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "SqliteJournal":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record_run(
        self, decisions: Sequence[Decision], policy: PolicyConfig, at: datetime
    ) -> int:
        started_at = _timestamp(at)
        policy_text = _policy_json(policy)
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO runs (started_at, policy) VALUES (?, ?)", (started_at, policy_text)
            )
            run_id = int(cursor.lastrowid or 0)
            previous = self._last_hash("decisions")
            for decision in decisions:
                values = (run_id, started_at, policy_text, *_decision_values(decision))
                previous = chain_hash(previous, _decision_content(values))
                self._db.execute(schema.INSERT_DECISION, (run_id, *values[3:], previous))
        return run_id

    def last_entry(self, invoice: str) -> JournalEntry | None:
        row = self._db.execute(
            schema.SELECT_DECISIONS + "WHERE d.invoice = ? ORDER BY d.id DESC LIMIT 1", (invoice,)
        ).fetchone()
        return _entry(row) if row else None

    def history(self, invoice: str) -> list[JournalEntry]:
        rows = self._db.execute(
            schema.SELECT_DECISIONS + "WHERE d.invoice = ? ORDER BY d.id", (invoice,)
        )
        return [_entry(row) for row in rows]

    def record_answer(self, answer: OwnerAnswer) -> None:
        values = (
            answer.invoice,
            answer.fingerprint,
            answer.verdict.value,
            answer.answered_by,
            _timestamp(answer.answered_at),
            answer.note,
        )
        with self._db:
            entry_hash = chain_hash(self._last_hash("answers"), _answer_content(values))
            self._db.execute(schema.INSERT_ANSWER, (*values, entry_hash))

    def latest_answer(self, invoice: str) -> OwnerAnswer | None:
        row = self._db.execute(
            schema.SELECT_ANSWERS + "WHERE invoice = ? ORDER BY id DESC LIMIT 1", (invoice,)
        ).fetchone()
        return _answer(row) if row else None

    def verify(self) -> None:
        previous = GENESIS
        for row in self._db.execute(schema.SELECT_DECISIONS + "ORDER BY d.id"):
            previous = _check(previous, _decision_content(row[:-1]), row[-1],
                              f"journal entry for {row[3]} in run {row[0]}")
        previous = GENESIS
        for row in self._db.execute(schema.SELECT_ANSWERS + "ORDER BY id"):
            previous = _check(previous, _answer_content(row[:-1]), row[-1],
                              f"owner answer for {row[0]} given at {row[4]}")

    def _last_hash(self, table: str) -> str:
        found = self._db.execute(
            f"SELECT entry_hash FROM {table} ORDER BY id DESC LIMIT 1"  # noqa: S608 (fixed names)
        ).fetchone()
        return found[0] if found else GENESIS


def _check(previous: str, content: dict, stored: str, label: str) -> str:
    expected = chain_hash(previous, content)
    if stored != expected:
        raise JournalError(f"{label} was altered")
    return expected


def _timestamp(at: datetime) -> str:
    if at.tzinfo is None:
        raise JournalError("journal timestamps must be timezone-aware")
    return at.isoformat()


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


def _decision_content(values: tuple) -> dict:
    return dict(zip(schema.DECISION_KEYS, values, strict=True))


def _answer_content(values: tuple) -> dict:
    return dict(zip(schema.ANSWER_KEYS, values, strict=True))


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


def _answer(row: tuple) -> OwnerAnswer:
    invoice, fp, verdict, answered_by, answered_at, note, _ = row
    return OwnerAnswer(
        invoice=invoice,
        fingerprint=fp,
        verdict=Verdict(verdict),
        answered_by=answered_by,
        answered_at=datetime.fromisoformat(answered_at),
        note=note,
    )
