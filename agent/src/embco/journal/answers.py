"""The owner answers an ASK, and the agent looks those answers up on the next run."""

from collections.abc import Sequence
from datetime import UTC, datetime

from embco.decision import Action, Decision, OwnerAnswer, Verdict
from embco.journal.base import DecisionJournal, JournalError


def answer_ask(
    journal: DecisionJournal,
    invoice: str,
    verdict: Verdict,
    answered_by: str,
    *,
    note: str = "",
    at: datetime | None = None,
) -> OwnerAnswer:
    """Record the owner's answer, tied to the last decision the agent showed for the invoice."""
    if not answered_by.strip():
        raise JournalError("an answer needs the name of who gave it")
    last = journal.last_entry(invoice)
    if last is None:
        raise JournalError(f"{invoice} has no decision to answer")
    if last.action is not Action.ASK:
        raise JournalError(f"{invoice} is not waiting for an answer (last decision: {last.action})")
    answer = OwnerAnswer(
        invoice=invoice,
        fingerprint=last.fingerprint,
        verdict=verdict,
        answered_by=answered_by.strip(),
        answered_at=at or datetime.now(UTC),
        note=note.strip(),
    )
    journal.record_answer(answer)
    return answer


def answers_for(journal: DecisionJournal, decisions: Sequence[Decision]) -> dict[str, OwnerAnswer]:
    found = {d.invoice: journal.latest_answer(d.invoice) for d in decisions}
    return {invoice: answer for invoice, answer in found.items() if answer is not None}
