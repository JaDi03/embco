"""Decision journal in a local SQLite file.

Append-only: triggers reject updates and deletes, and every entry is hash-chained to the one
before it in a single sequence, so an edit or removal made around the triggers is caught by
verify(). Only what changes is stored, so the file grows with events, not with runs.
"""

import json
import sqlite3
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import fields
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from embco.controls import Finding, Outcome, WalletChallenge, WalletProof
from embco.decision import Action, Decision, OwnerAnswer, PolicyConfig, Verdict, fingerprint
from embco.journal import schema
from embco.journal.base import JournalError
from embco.journal.changes import Change, ChangeKind
from embco.journal.models import GENESIS, JournalEntry, canonical, chain_hash, digest
from embco.llm.base import Explanation
from embco.payments.models import PaymentEvent, PaymentStatus

CLOSED_NOTE = "no longer among the unpaid invoices in the ERP"


class SqliteJournal:
    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(path, isolation_level=None)
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, schema.VERSION):
            self._db.close()
            raise JournalError(f"journal format {version} is not supported by this agent")
        self._db.executescript(schema.TABLES)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "SqliteJournal":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record_run(
        self,
        changes: Sequence[Change],
        closed: Sequence[str],
        policy: PolicyConfig,
        at: datetime,
    ) -> int:
        stamp = _timestamp(at)
        values = {f.name: str(getattr(policy, f.name)) for f in fields(policy)}
        policy_id = digest(values)
        counts = Counter(c.kind for c in changes)
        with self._transaction():
            head = self._head()
            run = (self._db.execute(schema.LAST_RUN).fetchone()[0] or 0) + 1
            if self._current_policy() != policy_id:
                body = {"policy": policy_id, "values": values}
                head = self._append(head, "POLICY", run, None, stamp, body)
            summary = {
                "policy": policy_id,
                "unpaid": len(changes),
                "new": counts[ChangeKind.NEW],
                "changed": counts[ChangeKind.CHANGED],
                "same": counts[ChangeKind.SAME],
                "closed": len(closed),
            }
            head = self._append(head, "RUN", run, None, stamp, summary)
            for change in changes:
                if change.kind is not ChangeKind.SAME:
                    body = _decision_body(change.decision)
                    head = self._append(head, "DECISION", run, change.decision.invoice, stamp, body)
            for invoice in closed:
                head = self._append(head, "CLOSED", run, invoice, stamp, {"note": CLOSED_NOTE})
        return run

    def last_entry(self, invoice: str) -> JournalEntry | None:
        row = self._db.execute(schema.LAST_OF_KIND, (invoice, "DECISION")).fetchone()
        return _entry(row) if row else None

    def history(self, invoice: str) -> list[JournalEntry]:
        return [_entry(row) for row in self._db.execute(schema.DECISIONS_OF, (invoice,))]

    def open_invoices(self) -> set[str]:
        return {row[0] for row in self._db.execute(schema.OPEN_INVOICES)}

    def record_answer(self, answer: OwnerAnswer) -> None:
        body = {
            "fingerprint": answer.fingerprint,
            "verdict": answer.verdict.value,
            "answered_by": answer.answered_by,
            "note": answer.note,
        }
        self._append_owner_entry("ANSWER", answer.answered_at, body, answer.invoice)

    def latest_answer(self, invoice: str) -> OwnerAnswer | None:
        row = self._db.execute(schema.LAST_OF_KIND, (invoice, "ANSWER")).fetchone()
        return _answer(row) if row else None

    def record_wallet_challenge(self, challenge: WalletChallenge) -> None:
        body = {
            "supplier": challenge.supplier,
            "wallet": challenge.wallet,
            "payer": challenge.payer,
            "nonce": challenge.nonce,
            "expires_at": _timestamp(challenge.expires_at),
        }
        self._append_owner_entry("CHALLENGE", challenge.issued_at, body)

    def latest_wallet_challenge(self, supplier: str) -> WalletChallenge | None:
        row = self._db.execute(schema.LAST_FOR_SUPPLIER, ("CHALLENGE", supplier)).fetchone()
        if not row:
            return None
        body = json.loads(row[1])
        return WalletChallenge(
            supplier=body["supplier"],
            wallet=body["wallet"],
            payer=body["payer"],
            nonce=body["nonce"],
            issued_at=datetime.fromisoformat(row[0]),
            expires_at=datetime.fromisoformat(body["expires_at"]),
        )

    def record_wallet_proof(self, proof: WalletProof) -> None:
        body = {
            "supplier": proof.supplier,
            "wallet": proof.wallet,
            "nonce": proof.nonce,
            "signature": proof.signature,
        }
        self._append_owner_entry("PROOF", proof.signed_at, body)

    def latest_wallet_proof(self, supplier: str) -> WalletProof | None:
        row = self._db.execute(schema.LAST_FOR_SUPPLIER, ("PROOF", supplier)).fetchone()
        if not row:
            return None
        body = json.loads(row[1])
        return WalletProof(
            supplier=body["supplier"],
            wallet=body["wallet"],
            nonce=body["nonce"],
            signature=body["signature"],
            signed_at=datetime.fromisoformat(row[0]),
        )

    def record_explanation(self, explanation: Explanation) -> None:
        body = {
            "fingerprint": explanation.fingerprint,
            "summary": explanation.summary,
            "next_step": explanation.next_step,
            "model": explanation.model,
        }
        self._append_owner_entry("EXPLANATION", explanation.created_at, body, explanation.invoice)

    def explanation_for(self, invoice: str, fingerprint: str) -> Explanation | None:
        """The stored explanation of exactly this decision, if the latest one is about it."""
        row = self._db.execute(schema.LAST_OF_KIND, (invoice, "EXPLANATION")).fetchone()
        if not row:
            return None
        body = json.loads(row[3])
        if body["fingerprint"] != fingerprint:
            return None
        return Explanation(
            invoice=invoice,
            fingerprint=body["fingerprint"],
            summary=body["summary"],
            next_step=body["next_step"],
            model=body["model"],
            created_at=datetime.fromisoformat(row[2]),
        )

    def record_payment(self, event: PaymentEvent) -> None:
        body = {
            "status": event.status.value,
            "supplier": event.supplier,
            "payee": event.payee,
            "amount": str(event.amount),
            "invoice_ref": event.invoice_ref,
            "attempt": event.attempt,
            "circle_tx_id": event.circle_tx_id,
            "tx_hash": event.tx_hash,
            "reason": event.reason,
        }
        self._append_owner_entry("PAYMENT", event.at, body, event.invoice)

    def latest_payment(self, invoice: str) -> PaymentEvent | None:
        row = self._db.execute(schema.LAST_OF_KIND, (invoice, "PAYMENT")).fetchone()
        return _payment(row) if row else None

    def payment_attempts(self, invoice: str) -> int:
        return self._db.execute(schema.PAYMENT_ATTEMPTS, (invoice,)).fetchone()[0]

    def pending_payments(self) -> list[PaymentEvent]:
        return [_payment(row) for row in self._db.execute(schema.PENDING_PAYMENTS)]

    def verify(self) -> None:
        previous = GENESIS
        for number, (kind, run, invoice, at, body, stored) in enumerate(
            self._db.execute(schema.ALL), start=1
        ):
            expected = chain_hash(previous, _content(kind, run, invoice, at, body))
            if stored != expected:
                subject = invoice or (f"run {run}" if run is not None else "owner entry")
                raise JournalError(f"journal entry {number} ({kind} {subject}) was altered")
            previous = expected

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def _append_owner_entry(
        self, kind: str, at: datetime, body: dict, invoice: str | None = None
    ) -> None:
        """Entries outside a run: owner answers, the wallet challenge exchange, payments."""
        stamp = _timestamp(at)
        with self._transaction():
            self._append(self._head(), kind, None, invoice, stamp, body)

    def _head(self) -> str:
        row = self._db.execute(schema.HEAD).fetchone()
        return row[0] if row else GENESIS

    def _current_policy(self) -> str | None:
        row = self._db.execute(schema.LAST_POLICY).fetchone()
        return json.loads(row[0])["policy"] if row else None

    def _append(
        self, previous: str, kind: str, run: int | None, invoice: str | None, at: str, body: dict
    ) -> str:
        text = canonical(body)
        entry_hash = chain_hash(previous, _content(kind, run, invoice, at, text))
        self._db.execute(schema.INSERT, (kind, run, invoice, at, text, entry_hash))
        return entry_hash


def _content(kind: str, run: int | None, invoice: str | None, at: str, body: str) -> dict:
    return {"kind": kind, "run": run, "invoice": invoice, "at": at, "body": body}


def _timestamp(at: datetime) -> str:
    if at.tzinfo is None:
        raise JournalError("journal timestamps must be timezone-aware")
    return at.isoformat()


def _decision_body(decision: Decision) -> dict:
    return {
        "supplier": decision.supplier,
        "amount": str(decision.amount),
        "due_date": decision.due_date.isoformat() if decision.due_date else None,
        "action": decision.action.value,
        "reasons": list(decision.reasons),
        "findings": [[f.control, f.outcome.value, f.reason] for f in decision.findings],
        "fingerprint": fingerprint(decision),
    }


def _entry(row: tuple) -> JournalEntry:
    run, invoice, at, text, entry_hash = row
    body = json.loads(text)
    due = body["due_date"]
    return JournalEntry(
        run_id=run,
        recorded_at=datetime.fromisoformat(at),
        invoice=invoice,
        supplier=body["supplier"],
        amount=Decimal(body["amount"]),
        due_date=date.fromisoformat(due) if due else None,
        action=Action(body["action"]),
        reasons=tuple(body["reasons"]),
        findings=tuple(Finding(c, Outcome(o), r) for c, o, r in body["findings"]),
        fingerprint=body["fingerprint"],
        entry_hash=entry_hash,
    )


def _payment(row: tuple) -> PaymentEvent:
    _, invoice, at, text, _ = row
    body = json.loads(text)
    return PaymentEvent(
        invoice=invoice,
        status=PaymentStatus(body["status"]),
        at=datetime.fromisoformat(at),
        supplier=body["supplier"],
        payee=body["payee"],
        amount=Decimal(body["amount"]),
        invoice_ref=body["invoice_ref"],
        attempt=body["attempt"],
        circle_tx_id=body["circle_tx_id"],
        tx_hash=body["tx_hash"],
        reason=body["reason"],
    )


def _answer(row: tuple) -> OwnerAnswer:
    _, invoice, at, text, _ = row
    body = json.loads(text)
    return OwnerAnswer(
        invoice=invoice,
        fingerprint=body["fingerprint"],
        verdict=Verdict(body["verdict"]),
        answered_by=body["answered_by"],
        answered_at=datetime.fromisoformat(at),
        note=body["note"],
    )
