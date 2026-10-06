"""Compare a decision with the last one the agent took on the same invoice."""

from dataclasses import dataclass
from enum import StrEnum

from embco.decision import Decision, fingerprint
from embco.journal.models import JournalEntry


class ChangeKind(StrEnum):
    NEW = "NEW"
    SAME = "SAME"
    CHANGED = "CHANGED"


@dataclass(frozen=True)
class Change:
    decision: Decision
    kind: ChangeKind
    previous: JournalEntry | None
    note: str


def compare(decision: Decision, previous: JournalEntry | None) -> Change:
    action = decision.action
    if previous is None:
        return Change(decision, ChangeKind.NEW, None, "first time the agent sees this invoice")
    run = previous.run_id
    if previous.fingerprint == fingerprint(decision):
        return Change(decision, ChangeKind.SAME, previous, f"same as run {run}: {action}")
    if previous.action is action:
        note = f"still {action}, but the amount or the reasons changed since run {run}"
    else:
        note = f"was {previous.action} in run {run}, now {action}"
    return Change(decision, ChangeKind.CHANGED, previous, note)
