"""The owner answers an ASK on the invoice itself, in the ERP, instead of with a command.

A mark counts only when it was written by one of the configured owner accounts (the ERP's own
change history says who), for the question the agent last showed for that invoice, and only
once: a mark already used for an earlier question must be written again for a new one.
"""

import logging
from collections.abc import Sequence
from datetime import datetime

from agent.guardrails.rules import Action, Decision, OwnerAnswer, Verdict, fingerprint
from agent.memory.base import DecisionJournal
from services.erp import LedgerAdapter, LedgerError

log = logging.getLogger("embco")

VERDICTS = {"approve": Verdict.APPROVE, "reject": Verdict.REJECT}


def answers_from_erp(
    journal: DecisionJournal,
    ledger: LedgerAdapter,
    decisions: Sequence[Decision],
    owners: Sequence[str],
    at: datetime,
) -> dict[str, OwnerAnswer]:
    """Read the owner's marks on open questions and save them as answers like any other."""
    allowed = {o.strip().lower() for o in owners if o.strip()}
    found: dict[str, OwnerAnswer] = {}
    for decision in decisions:
        if not allowed or decision.action is not Action.ASK:
            continue
        last = journal.last_entry(decision.invoice)
        if last is None or last.action is not Action.ASK:
            continue  # the agent has not shown this question yet
        if last.fingerprint != fingerprint(decision):
            continue
        try:
            mark = ledger.owner_mark(decision.invoice)
        except LedgerError as error:
            log.warning("owner answer for %s not read this cycle: %s", decision.invoice, error)
            continue
        if mark is None:
            continue
        verdict = VERDICTS.get(mark.verdict.lower())
        if verdict is None or mark.set_by.lower() not in allowed:
            log.warning("owner answer for %s ignored: set by %s, not an owner account",
                        decision.invoice, mark.set_by)
            continue
        tag = f"ERP change {mark.change_id}"
        previous = journal.latest_answer(decision.invoice)
        if previous and tag in previous.note:
            continue  # already answered an earlier question
        answer = OwnerAnswer(
            invoice=decision.invoice, fingerprint=last.fingerprint, verdict=verdict,
            answered_by=mark.set_by, answered_at=at,
            note=f"{mark.note} ({tag})" if mark.note else tag,
        )
        journal.record_answer(answer)
        found[decision.invoice] = answer
    return found
