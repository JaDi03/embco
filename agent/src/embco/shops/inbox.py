"""What people sent the shop's agent from the dashboard, waiting for its next cycle: wallet
signatures from suppliers and the owner's answers to its questions.

The hub drops each one as a file in `<shop>/inbox/` (answers in `<shop>/inbox/answers/`); only
the agent writes the shop's memory, so it checks and records them at the start of its next
cycle. A file is removed once handled; a signature stays if the ERP cannot be reached.
"""

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from embco.decision import Verdict
from embco.journal import DecisionJournal, JournalError, answer_ask, submit_wallet_signature
from embco.ledger import LedgerAdapter, LedgerError

log = logging.getLogger("embco")

INBOX = "inbox"
ANSWERS = "answers"
ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"


def _name(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _drop(folder: Path, name: str, item: dict[str, str]) -> None:
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = folder / f"{name}.tmp"
    tmp.write_text(json.dumps(item), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, folder / f"{name}.json")


def drop_signature(folder: Path, supplier: str, signature: str, at: datetime) -> None:
    """One file per supplier: a newer signature replaces one the agent has not read yet."""
    _drop(folder / INBOX, _name(supplier),
          {"supplier": supplier, "signature": signature, "received_at": at.isoformat()})


def signature_waiting(folder: Path, supplier: str) -> bool:
    """A signature from this supplier is in the inbox and the agent has not read it yet."""
    return (folder / INBOX / f"{_name(supplier)}.json").exists()


def take_signatures(
    folder: Path,
    journal: DecisionJournal,
    ledger: LedgerAdapter,
    *,
    at: datetime | None = None,
) -> dict[str, dict[str, str]]:
    """The outcome of each signature handled now, by supplier."""
    now = at or datetime.now(UTC)
    results: dict[str, dict[str, str]] = {}
    for path in sorted((folder / INBOX).glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            supplier, signature = str(item["supplier"]), str(item["signature"])
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("unreadable signature file %s removed", path.name)
            path.unlink(missing_ok=True)
            continue
        try:
            proof = submit_wallet_signature(journal, ledger, supplier, signature, at=now)
        except LedgerError as error:
            log.warning("signature of %s left for the next cycle: %s", supplier, error)
            continue
        except JournalError as error:
            results[supplier] = {"result": REJECTED, "at": now.isoformat(), "reason": str(error)}
        else:
            results[supplier] = {"result": ACCEPTED, "at": now.isoformat(),
                                 "wallet": proof.wallet}
            log.info("wallet %s of %s proven by signature", proof.wallet, supplier)
        path.unlink(missing_ok=True)
    return results


def drop_answer(folder: Path, invoice: str, verdict: Verdict, fingerprint: str, answered_by: str,
                note: str, at: datetime) -> None:
    """One file per invoice: a newer answer replaces one the agent has not read yet."""
    _drop(folder / INBOX / ANSWERS, _name(invoice),
          {"invoice": invoice, "verdict": verdict.value, "fingerprint": fingerprint,
           "answered_by": answered_by, "note": note, "received_at": at.isoformat()})


def answer_waiting(folder: Path, invoice: str) -> bool:
    return (folder / INBOX / ANSWERS / f"{_name(invoice)}.json").exists()


def take_answers(
    folder: Path, journal: DecisionJournal, *, at: datetime | None = None
) -> dict[str, dict[str, str]]:
    """Record each answer, only for the exact question the owner saw: if the agent's decision
    changed since (new amount, new reasons), the answer is refused and the owner looks again."""
    now = at or datetime.now(UTC)
    results: dict[str, dict[str, str]] = {}
    for path in sorted((folder / INBOX / ANSWERS).glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            invoice, verdict = str(item["invoice"]), Verdict(item["verdict"])
            shown, by = str(item["fingerprint"]), str(item["answered_by"])
            note = str(item.get("note") or "")
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("unreadable answer file %s removed", path.name)
            path.unlink(missing_ok=True)
            continue
        last = journal.last_entry(invoice)
        try:
            if last is None or last.fingerprint != shown:
                raise JournalError("the agent's question changed since you answered; look again")
            answer_ask(journal, invoice, verdict, by, note=note, at=now)
        except JournalError as error:
            results[invoice] = {"result": REJECTED, "verdict": verdict.value,
                                "at": now.isoformat(), "reason": str(error)}
        else:
            results[invoice] = {"result": ACCEPTED, "verdict": verdict.value,
                                "at": now.isoformat()}
            log.info("owner answer %s recorded for %s", verdict.value, invoice)
        path.unlink(missing_ok=True)
    return results
