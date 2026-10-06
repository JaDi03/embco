"""The contract any journal store follows. The agent never depends on where it is stored."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from embco.controls import WalletChallenge, WalletProof
from embco.decision import OwnerAnswer, PolicyConfig
from embco.journal.changes import Change
from embco.journal.models import JournalEntry


class JournalError(Exception):
    pass


class DecisionJournal(Protocol):
    def record_run(
        self,
        changes: Sequence[Change],
        closed: Sequence[str],
        policy: PolicyConfig,
        at: datetime,
    ) -> int:
        """Store one run in a single step and return its number.

        The run itself is always recorded; a decision only when it is new or changed, the
        policy only when it differs from the last one, and every invoice in `closed`.
        """
        ...

    def last_entry(self, invoice: str) -> JournalEntry | None: ...

    def history(self, invoice: str) -> list[JournalEntry]:
        """Every distinct decision taken on the invoice, oldest first."""
        ...

    def open_invoices(self) -> set[str]:
        """Invoices whose last recorded event is a decision, not a closure."""
        ...

    def record_answer(self, answer: OwnerAnswer) -> None: ...

    def latest_answer(self, invoice: str) -> OwnerAnswer | None: ...

    def record_wallet_challenge(self, challenge: WalletChallenge) -> None: ...

    def latest_wallet_challenge(self, supplier: str) -> WalletChallenge | None: ...

    def record_wallet_proof(self, proof: WalletProof) -> None: ...

    def latest_wallet_proof(self, supplier: str) -> WalletProof | None: ...

    def verify(self) -> None:
        """Raise JournalError if any entry was changed or removed after it was written."""
        ...
