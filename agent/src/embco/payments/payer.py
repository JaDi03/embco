"""Pay the invoices the plan says to pay now, through the shop contract, with the agent's wallet.

Every payment, in this order:
1. Re-read the invoice and the supplier from the ERP: still unpaid for the same amount, in USD,
   to a valid wallet.
2. Skip it if the contract already marks the invoice paid, or if the contract's weekly cap
   has no room left (it waits; nothing is recorded).
3. Simulate the exact call through the Arc node. A call that would revert is not sent: it is
   recorded as BLOCKED with the contract's reason.
4. Send it through Circle with an idempotency key derived from the payment and the attempt
   number, so a retry after a crash returns the same transaction instead of a new one.
5. Follow it until Circle reports a final state, now or in a later cycle.
6. Once final, write it into the ERP as a payment entry (when a writer is given), now or in a
   later cycle if the ERP is down. The invoice then leaves the unpaid list.

The contract is the real limit: it refuses a payee the owner has not approved, an amount over
the per-payment limit or the weekly cap, and an invoice already paid.
"""

import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from eth_utils import to_checksum_address

from embco.circle import CircleClient, CircleError, CircleTransaction
from embco.controls.payee_wallet import is_evm_address
from embco.decision import Decision, PaymentPlan
from embco.ledger import LedgerAdapter, LedgerError, PaymentWriter
from embco.ledger.models import SettledPayment
from embco.payments.chain import ArcRpc, ChainError, Reverted
from embco.payments.encoding import (
    MEMO_CONTRACT,
    EncodingError,
    invoice_ref,
    memo_call,
    pay_call,
    usdc_units,
)
from embco.payments.models import PaymentEvent, PaymentStatus

log = logging.getLogger("embco.payments")

PAYABLE_CURRENCY = "USD"  # the shop contract pays USDC
IN_FLIGHT = (PaymentStatus.SUBMITTED, PaymentStatus.COMPLETE, PaymentStatus.RECORDED)
MAX_ATTEMPTS = 3  # transactions sent for one invoice before it waits for a person


class PaymentSetupError(Exception):
    """The agent cannot pay at all (wrong wallet, contract or node). Nothing is sent."""


class PaymentBook(Protocol):
    """The part of the journal payments use."""

    def record_payment(self, event: PaymentEvent) -> None: ...

    def latest_payment(self, invoice: str) -> PaymentEvent | None: ...

    def payment_attempts(self, invoice: str) -> int: ...

    def pending_payments(self) -> list[PaymentEvent]: ...

    def unrecorded_payments(self) -> list[PaymentEvent]: ...


@dataclass(frozen=True)
class Settlement:
    events: tuple[PaymentEvent, ...] = ()
    waiting: tuple[str, ...] = ()  # invoices left for later: no room under the weekly cap
    problem: str = ""  # why nothing could be paid this cycle, if so


def idempotency_key(shop: str, ref: bytes, payee: str, units: int, attempt: int) -> str:
    """A UUID v4 shaped key that is the same for the same payment attempt, on any machine."""
    text = f"embco-payment-v1|{shop.lower()}|{ref.hex()}|{payee.lower()}|{units}|{attempt}"
    return str(uuid.UUID(bytes=hashlib.sha256(text.encode()).digest()[:16], version=4))


@dataclass
class Payer:
    ledger: LedgerAdapter
    chain: ArcRpc
    circle: CircleClient
    shop: str
    wallet_id: str
    wait: timedelta = timedelta(seconds=60)
    poll_every: float = 2.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    writer: PaymentWriter | None = None
    _agent: str | None = None

    def agent_address(self) -> str:
        """The wallet's address, checked once against the contract's agent."""
        if self._agent is None:
            address = to_checksum_address(self.circle.get_wallet(self.wallet_id).address)
            on_chain = self.chain.agent_of(self.shop)
            if on_chain != address:
                raise PaymentSetupError(
                    f"the shop contract's agent is {on_chain}, not this wallet ({address}); "
                    "the owner sets it in the dashboard"
                )
            self._agent = address
        return self._agent

    def settle(self, plan: PaymentPlan, book: PaymentBook) -> Settlement:
        agent = self.agent_address()
        events: list[PaymentEvent] = []
        for pending in book.pending_payments():
            events.extend(self._follow(pending, book, wait=False))
        remaining = self.chain.remaining_this_week(self.shop)
        waiting: list[str] = []
        for decision in plan.pay_now:
            last = book.latest_payment(decision.invoice)
            if last and last.status in IN_FLIGHT:
                continue
            try:
                outcome = self._pay(decision, book, agent, remaining, last)
            except (ChainError, CircleError, LedgerError) as error:
                log.warning("payment of %s skipped this cycle: %s", decision.invoice, error)
                continue
            if outcome is None:
                waiting.append(decision.invoice)
                continue
            for event in outcome:
                events.append(event)
                if event.status is PaymentStatus.SUBMITTED:
                    remaining -= usdc_units(event.amount)
        if self.writer is not None:
            for done in book.unrecorded_payments():
                events.extend(self._record(done, book))
        return Settlement(events=tuple(events), waiting=tuple(waiting))

    def _record(self, done: PaymentEvent, book: PaymentBook) -> list[PaymentEvent]:
        if self.writer is None or not done.tx_hash:
            return []
        note = (f"PAY decided by the embco agent; invoice ref {done.invoice_ref}; "
                f"Circle transaction {done.circle_tx_id}")
        try:
            entry = self.writer.record_payment(SettledPayment(
                invoice=done.invoice, supplier=done.supplier, amount=done.amount,
                paid_on=done.at.date(), tx_hash=done.tx_hash, payee_wallet=done.payee,
                note=note,
            ))
        except LedgerError as error:
            log.warning("payment of %s not recorded in the ERP yet: %s", done.invoice, error)
            return []
        event = replace(done, status=PaymentStatus.RECORDED, at=self.clock(), erp_entry=entry,
                        reason="")
        book.record_payment(event)
        log.info("payment of %s recorded in the ERP as %s", done.invoice, entry)
        return [event]

    def _pay(
        self,
        decision: Decision,
        book: PaymentBook,
        agent: str,
        remaining: int,
        last: PaymentEvent | None,
    ) -> list[PaymentEvent] | None:
        name = decision.invoice
        ref = invoice_ref(name)
        base = PaymentEvent(
            invoice=name, status=PaymentStatus.BLOCKED, at=self.clock(),
            supplier=decision.supplier, payee="", amount=decision.amount,
            invoice_ref="0x" + ref.hex(),
        )

        def blocked(reason: str) -> list[PaymentEvent]:
            if last and last.status is PaymentStatus.BLOCKED and last.reason == reason:
                return []  # same reason as last time: nothing new to remember
            event = replace(base, reason=reason)
            book.record_payment(event)
            log.warning("payment of %s blocked: %s", name, reason)
            return [event]

        invoice = self.ledger.get_purchase_invoice(name)
        if invoice.currency != PAYABLE_CURRENCY:
            return blocked(f"the invoice is in {invoice.currency}; the contract pays USD (USDC)")
        if invoice.outstanding_amount != decision.amount:
            return blocked("the amount due changed since the decision; deciding again next cycle")
        wallet = self.ledger.get_supplier(decision.supplier).wallet_address or ""
        if not is_evm_address(wallet):
            return blocked("the supplier has no valid wallet on file")
        payee = to_checksum_address(wallet)
        base = replace(base, payee=payee)
        try:
            units = usdc_units(decision.amount)
        except EncodingError as error:
            return blocked(str(error))
        if self.chain.is_paid(self.shop, ref):
            event = replace(base, status=PaymentStatus.COMPLETE,
                            reason="the contract already marks this invoice paid")
            book.record_payment(event)
            return [event]
        if units > remaining:
            return None
        data = memo_call(self.shop, pay_call(payee, units, ref), ref, name)
        try:
            self.chain.call(MEMO_CONTRACT, data, sender=agent)
        except Reverted as error:
            return blocked(f"the contract would refuse it: {error.reason}")
        attempt = book.payment_attempts(name)
        if attempt >= MAX_ATTEMPTS:
            return blocked(f"{attempt} transactions for this invoice failed; check it by hand")
        tx = self.circle.execute_contract(
            self.wallet_id, MEMO_CONTRACT, data,
            idempotency_key=idempotency_key(self.shop, ref, payee, units, attempt),
            ref_id=name,
        )
        sent = replace(base, status=PaymentStatus.SUBMITTED, at=self.clock(), attempt=attempt,
                       circle_tx_id=tx.id)
        book.record_payment(sent)
        log.info("payment of %s sent: %s USDC to %s (Circle %s)", name, decision.amount, payee,
                 tx.id)
        return [sent, *self._follow(sent, book, wait=True)]

    def _follow(self, sent: PaymentEvent, book: PaymentBook, *, wait: bool) -> list[PaymentEvent]:
        if not sent.circle_tx_id:
            return []
        deadline = time.monotonic() + (self.wait.total_seconds() if wait else 0)
        while True:
            try:
                tx = self.circle.get_transaction(sent.circle_tx_id)
            except CircleError as error:
                log.warning("could not read the state of %s: %s", sent.invoice, error)
                return []
            if tx.finished or time.monotonic() >= deadline:
                break
            self.sleep(self.poll_every)
        if not tx.finished:
            return []
        event = _final(sent, tx, self.clock())
        book.record_payment(event)
        log.info("payment of %s %s%s", sent.invoice, event.status.value.lower(),
                 f": {event.reason}" if event.reason else "")
        return [event]


def _final(sent: PaymentEvent, tx: CircleTransaction, at: datetime) -> PaymentEvent:
    if tx.state == "COMPLETE":
        return replace(sent, status=PaymentStatus.COMPLETE, at=at, tx_hash=tx.tx_hash, reason="")
    reason = f"Circle state {tx.state}" + (f": {tx.error_reason}" if tx.error_reason else "")
    return replace(sent, status=PaymentStatus.FAILED, at=at, tx_hash=tx.tx_hash, reason=reason)


def paid_or_sent(book: PaymentBook, decisions: list[Decision]) -> set[str]:
    """Invoices the agent has already paid or sent, so they are not planned again."""
    result = set()
    for d in decisions:
        last = book.latest_payment(d.invoice)
        if last and last.status in IN_FLIGHT:
            result.add(d.invoice)
    return result

