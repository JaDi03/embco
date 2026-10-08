"""The contract any journal store follows. The agent never depends on where it is stored."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from embco.controls import WalletChallenge, WalletProof
from embco.decision import OwnerAnswer, PolicyConfig
from embco.journal.changes import Change
from embco.journal.models import JournalEntry
from embco.llm.base import Explanation
from embco.payments.models import PaymentEvent


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

    def record_explanation(self, explanation: Explanation) -> None: ...

    def explanation_for(self, invoice: str, fingerprint: str) -> Explanation | None: ...

    def record_payment(self, event: PaymentEvent) -> None: ...

    def latest_payment(self, invoice: str) -> PaymentEvent | None: ...

    def payment_attempts(self, invoice: str) -> int:
        """How many transactions were sent to Circle for the invoice."""
        ...

    def pending_payments(self) -> list[PaymentEvent]:
        """Payments sent to Circle whose final result is not recorded yet."""
        ...

    def unrecorded_payments(self) -> list[PaymentEvent]:
        """Payments final on chain, with a transaction, not yet written into the ERP."""
        ...

    def latest_payments(self) -> list[PaymentEvent]:
        """The last event of every invoice the agent tried to pay, oldest first."""
        ...

    def verify(self) -> None:
        """Raise JournalError if any entry was changed or removed after it was written."""
        ...
