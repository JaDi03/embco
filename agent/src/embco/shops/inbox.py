"""Wallet signatures suppliers sent from their page, waiting for the shop's agent.

The hub drops each one as a file in `<shop>/inbox/`; only the agent writes the shop's memory, so
it checks and records them at the start of its next cycle. A file is removed once handled; if
the ERP cannot be reached it stays for the next cycle.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from embco.journal import DecisionJournal, JournalError, submit_wallet_signature
from embco.ledger import LedgerAdapter, LedgerError

log = logging.getLogger("embco")

INBOX = "inbox"
ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"


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
