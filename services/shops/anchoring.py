"""When the shop's journal is anchored on Arc: after money moved, and at least once a day.

Decided from the journal itself, so a payment made in a chat turn, or before a restart, is
anchored by the next check. A failed anchor is tried again at the next check; it never stops one.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from agent.memory import DecisionJournal
from services.anchor import JOURNAL_ANCHOR, Anchor, JournalAnchors, journal_root
from services.circle import CircleError
from services.payments import ChainError, Payer, PaymentSetupError

DAY = timedelta(days=1)

log = logging.getLogger("embco")


def anchors_for(payer: Payer | None, address: str = JOURNAL_ANCHOR) -> JournalAnchors | None:
    """The shop's anchors, when the agent pays on Arc and the registry is deployed."""
    if payer is None or not address:
        return None
    return JournalAnchors(payer.chain, payer.shop, address=address, payer=payer)


def money_moved(entries: Sequence[tuple[str, Any, Any, Any, dict]]) -> bool:
    """A payment step, or a session that moved funds (Earn, bringing funds), among `entries`."""
    return any(kind == "PAYMENT" or (kind == "SESSION" and body.get("funds_moved"))
               for kind, _run, _invoice, _at, body in entries)


def due(last: Anchor, entries: Sequence[tuple[str, Any, Any, Any, dict]],
        now: datetime) -> bool:
    if len(entries) <= last.size:
        return False
    if money_moved(entries[last.size:]):
        return True
    return last.anchored_at is None or now - last.anchored_at >= DAY


def anchor_journal(journal: DecisionJournal, anchors: JournalAnchors | None,
                   now: datetime) -> dict[str, Any] | None:
    """Anchor the whole journal when it is due; the activity event to show, or None."""
    if anchors is None:
        return None
    try:
        last = anchors.latest()
        if not due(last, journal.entries(), now):
            return None
        hashes = journal.entry_hashes()
        done = anchors.write(journal_root(hashes), len(hashes))
    except (ChainError, CircleError, PaymentSetupError) as error:
        log.warning("journal not anchored on Arc, trying again next check: %s", error)
        return None
    return {"at": now.isoformat(), "kind": "anchor",
            "text": f"Journal anchored on Arc: {done['size']} entries, root "
                    f"{done['root'][:12]}...",
            "anchor_tx": done["tx"]}  # not "tx": the feed shows every tx as money that moved
