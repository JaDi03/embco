"""The contract any journal store follows. The agent never depends on where it is stored."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from embco.decision import Decision, PolicyConfig
from embco.journal.models import JournalEntry


class JournalError(Exception):
    pass


class DecisionJournal(Protocol):
    def record_run(
        self, decisions: Sequence[Decision], policy: PolicyConfig, at: datetime
    ) -> int:
        """Store every decision of one run in a single step and return the run id."""
        ...

    def last_entry(self, invoice: str) -> JournalEntry | None: ...

    def history(self, invoice: str) -> list[JournalEntry]:
        """Every decision ever taken on the invoice, oldest first."""
        ...

    def verify(self) -> None:
        """Raise JournalError if any entry was changed after it was written."""
        ...
