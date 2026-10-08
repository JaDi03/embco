"""Wallet signatures suppliers sent from their page, waiting for the shop's agent.

The hub drops each one as a file in `<shop>/inbox/`; only the agent writes the shop's memory, so
it checks and records them at the start of its next cycle. A file is removed once handled; if
the ERP cannot be reached it stays for the next cycle.
"""

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from embco.journal import DecisionJournal, JournalError, submit_wallet_signature
from embco.ledger import LedgerAdapter, LedgerError

log = logging.getLogger("embco")

INBOX = "inbox"
ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"


def drop_signature(folder: Path, supplier: str, signature: str, at: datetime) -> None:
    """One file per supplier: a newer signature replaces one the agent has not read yet."""
    inbox = folder / INBOX
    inbox.mkdir(mode=0o700, exist_ok=True)
    name = hashlib.sha256(supplier.encode()).hexdigest()[:16]
    tmp = inbox / f"{name}.tmp"
    tmp.write_text(json.dumps({"supplier": supplier, "signature": signature,
                               "received_at": at.isoformat()}), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, inbox / f"{name}.json")


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
