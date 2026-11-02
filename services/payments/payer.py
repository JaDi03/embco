"""Pay the invoices the plan says to pay now, through the shop contract, with the agent's wallet.

Every payment, in this order:
1. Re-read the invoice and the supplier from the ERP: still submitted, for the same supplier,
   not on hold (the invoice, or payments to the supplier), unpaid for the same amount in the
   same currency, to a valid wallet. One in another currency is paid in USDC at the rate the
   decision converted it with, and only when the ERP can write that payment down.
2. Skip it if the contract already marks the invoice paid, or if the contract's weekly cap
   has no room left (it waits; nothing is recorded).
3. Skip it if the owner has not approved the wallet in the contract yet: it is listed for the
   dashboard, where the owner approves it with one signature.
   An invoice the agent asks about only because its wallet is new is listed the same way, and
   that one signature is also the owner's answer: the owner approves the first payment once.
4. Simulate the exact call through the Arc node. A call that would revert is not sent: it is
   recorded as BLOCKED with the contract's reason.
5. Write it in the journal, with the agent's reason, then send it through Circle with an
   idempotency key derived from the payment and the attempt number, so a retry after a crash
   returns the same transaction instead of a new one. When Circle's answer is lost (a dropped
   connection, a restart), that entry stays: the contract's Paid event says whether it went
   through, and a retry carries the same key.
6. Follow it until Circle reports a final state, now or in a later cycle.
7. Once final, write it into the ERP as a payment entry (when a writer is given), now or in a
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
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from eth_utils import to_checksum_address

from agent.guardrails.controls.base import Outcome, fmt
from agent.guardrails.controls.payee_wallet import PayeeWallet, is_evm_address
from agent.guardrails.rules import Action, Decision, OwnerAnswer, PaymentPlan, Verdict, fingerprint
from services.circle import CircleClient, CircleError, CircleTransaction, CircleUnanswered
from services.erp import LedgerAdapter, LedgerError, PaymentWriter
from services.erp.models import SettledPayment
from services.fx.rates import PAYABLE_CURRENCY
from services.payments.approvals import write_approvals
from services.payments.chain import ArcRpc, ChainError, Reverted
from services.payments.encoding import (
    USDC_DECIMALS,
    EncodingError,
    invoice_ref,
    pay_call,
    usdc_units,
)
from services.payments.models import PaymentEvent, PaymentStatus, PendingApproval

log = logging.getLogger("services.payments")

SUBMITTED_DOC = 1  # an ERP document in force: not a draft (0), not cancelled (2)
NATIVE_DECIMALS = 18  # Arc's native balance, which pays network fees in USDC
IN_FLIGHT = (PaymentStatus.SUBMITTED, PaymentStatus.COMPLETE, PaymentStatus.RECORDED)
FOREIGN_REF = ("the contract already has a payment with this invoice's reference that the agent "
               "did not send; check it on the explorer before paying")


def unanswered(event: PaymentEvent | None) -> bool:
    """Written just before Circle was asked, and Circle's answer was never seen."""
    return (event is not None and event.status is PaymentStatus.SUBMITTED
            and not event.circle_tx_id)


def settled_or_sent(event: PaymentEvent | None, book: "PaymentBook") -> bool:
    """The agent paid or sent this invoice. A "complete" with no transaction and no attempt was
    only the contract saying the reference was paid (by someone else, or by an invoice with the
    same name in another ERP): that is not a payment the agent made. One whose Circle answer was
    lost is not either: paying it again checks the contract first and repeats the same request."""
    if event is None or event.status not in IN_FLIGHT or unanswered(event):
        return False
    if event.status is PaymentStatus.COMPLETE and not event.tx_hash:
        return book.payment_attempts(event.invoice) > 0
    return True
MAX_ATTEMPTS = 3  # transactions sent for one invoice before it waits for a person
CHAIN_APPROVER = "owner (approved the wallet in the contract)"
CLOCK_SKEW = timedelta(minutes=5)  # how far before its journal entry a lost payment is looked for


def approve_wallet_reason(payee: str) -> str:
    return f"waiting for the owner to approve wallet {payee} in the dashboard"


def answer_by_approval_reason(payee: str) -> str:
    return f"{approve_wallet_reason(payee)}; that approval also answers the question"


def asks_only_about_wallet(decision: Decision) -> bool:
    """An ASK whose only open question is a wallet never paid before."""
    asking = [f for f in decision.findings if f.outcome is Outcome.ASK]
    return (decision.action is Action.ASK and bool(asking)
            and all(f.control == PayeeWallet.name for f in asking))


class PaymentSetupError(Exception):
    """The agent cannot pay at all (wrong wallet, contract or node). Nothing is sent."""


class PaymentRefused(Exception):
    """Nothing was sent for this invoice; the message is the real reason, for the agent."""


class PaymentBook(Protocol):
    """The part of the journal payments use."""

    def record_payment(self, event: PaymentEvent) -> None: ...

    def latest_payment(self, invoice: str) -> PaymentEvent | None: ...

    def wallet_noticed_at(self, supplier: str, wallet: str) -> datetime | None: ...

    def payment_attempts(self, invoice: str) -> int: ...

    def pending_payments(self) -> list[PaymentEvent]: ...

    def unrecorded_payments(self) -> list[PaymentEvent]: ...


@dataclass(frozen=True)
class Settlement:
    events: tuple[PaymentEvent, ...] = ()
    waiting: tuple[str, ...] = ()  # invoices left for later: no room under the weekly cap
    problem: str = ""  # why nothing could be paid this cycle, if so
    needs_approval: tuple[PendingApproval, ...] = ()  # wallets the owner must approve on chain


def _converted(decision: Decision) -> dict:
    """What a payment keeps of an invoice in another currency: its amount and the rate."""
    if decision.currency == PAYABLE_CURRENCY:
        return {}
    return {"invoice_amount": decision.billed, "currency": decision.currency,
            "rate": decision.rate.per_usd if decision.rate else None}


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
    approvals_file: Path | None = None
    ref_scope: str = ""  # the ERP and company the invoice names belong to
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

    def pay(self, decision: Decision, book: PaymentBook,
            session: str | None = None, why: str = "") -> list[PaymentEvent]:
        """Pay one invoice now, because the agent chose to: what happened, in order (sent, its
        final state, written in the ERP). Raises PaymentRefused with the real reason when
        nothing was sent: a precondition, the contract's answer to the simulation, or Circle's
        own error, word for word. `session` is the agent session that chose it and `why` its
        reason, kept with the payment before Circle is asked."""
        try:
            agent = self.agent_address()
            last = book.latest_payment(decision.invoice)
            if settled_or_sent(last, book):
                raise PaymentRefused("the agent already paid or sent this invoice")
            remaining = self.chain.remaining_this_week(self.shop)
            outcome = self._pay(decision, book, agent, remaining, last, [], session, why)
        except (PaymentSetupError, ChainError, CircleError, LedgerError) as error:
            raise PaymentRefused(str(error)) from None
        if outcome is None:
            left = fmt(Decimal(remaining).scaleb(-USDC_DECIMALS))
            raise PaymentRefused(f"the contract lets the agent pay only {left} USDC more this "
                                 "week")
        if not outcome or outcome[-1].status is PaymentStatus.BLOCKED:
            now = book.latest_payment(decision.invoice)
            raise PaymentRefused(now.reason if now and now.reason else "not sent")
        events = list(outcome)
        if events[-1].status is PaymentStatus.COMPLETE and events[-1].tx_hash:
            events.extend(self._record(events[-1], book))
        return events

    def status(self, invoice: str, book: PaymentBook) -> list[PaymentEvent]:
        """The latest state of the agent's payment of this invoice, looked up again when it is
        still on its way (and written in the ERP once final). Empty when there is none."""
        last = book.latest_payment(invoice)
        if last is None:
            return []
        events = [last]
        if last.status is PaymentStatus.SUBMITTED:
            events = self._follow(last, book, wait=False) or [last]
        if events[-1].status is PaymentStatus.COMPLETE and events[-1].tx_hash:
            events.extend(self._record(events[-1], book))
        return events

    def wallet_info(self) -> dict[str, str]:
        """The agent's own wallet: its address, its balance for network fees, whether the shop
        contract has it as its agent and whether the contract is paused."""
        address = to_checksum_address(self.circle.get_wallet(self.wallet_id).address)
        fees = self.chain.fee_balance(address)
        return {
            "address": address,
            "balance_for_network_fees_usdc": fmt(Decimal(fees).scaleb(-NATIVE_DECIMALS)),
            "is_the_contracts_agent": "yes" if self.chain.agent_of(self.shop) == address
            else "no: the owner sets it in the dashboard (Make it the agent)",
            "contract_paused": "yes" if self.chain.is_paused(self.shop) else "no",
        }

    def reconcile(self, decisions: list[Decision], book: PaymentBook) -> Settlement:
        """What needs no decision, every cycle: follow payments already sent to their final
        state, write finished ones in the ERP, and list the wallets waiting for the owner's
        approval in the contract. Nothing new is paid here; only the agent pays."""
        events: list[PaymentEvent] = []
        for sent in book.pending_payments():
            events.extend(self._follow(sent, book, wait=False))
        pending: list[PendingApproval] = []
        for decision in decisions:
            try:
                if asks_only_about_wallet(decision):
                    events.extend(self._list_for_approval(decision, book, pending))
                elif decision.action is Action.PAY:
                    self._list_unapproved(decision, pending)
            except (ChainError, LedgerError) as error:
                log.warning("wallet of %s not listed this cycle: %s", decision.invoice, error)
        if self.writer is not None:
            for done in book.unrecorded_payments():
                events.extend(self._record(done, book))
        if self.approvals_file is not None:
            write_approvals(self.approvals_file, self.shop, pending, self.clock())
        return Settlement(events=tuple(events), needs_approval=tuple(pending))

    def _list_unapproved(self, decision: Decision, pending: list[PendingApproval]) -> None:
        wallet = self.ledger.get_supplier(decision.supplier).wallet_address or ""
        if is_evm_address(wallet) and not self.chain.is_approved(self.shop,
                                                                  to_checksum_address(wallet)):
            pending.append(PendingApproval(wallet=to_checksum_address(wallet),
                                           invoice=decision.invoice, amount=decision.amount))

    def settle(self, plan: PaymentPlan, book: PaymentBook) -> Settlement:
        agent = self.agent_address()
        events: list[PaymentEvent] = []
        for pending in book.pending_payments():
            events.extend(self._follow(pending, book, wait=False))
        remaining = self.chain.remaining_this_week(self.shop)
        waiting: list[str] = []
        pending: list[PendingApproval] = []
        for decision in plan.pay_now:
            last = book.latest_payment(decision.invoice)
            if settled_or_sent(last, book):
                continue
            try:
                outcome = self._pay(decision, book, agent, remaining, last, pending)
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
        for decision in plan.asked:
            if not asks_only_about_wallet(decision):
                continue
            try:
                events.extend(self._list_for_approval(decision, book, pending))
            except (ChainError, LedgerError) as error:
                log.warning("wallet of %s not listed this cycle: %s", decision.invoice, error)
        if self.writer is not None:
            for done in book.unrecorded_payments():
                events.extend(self._record(done, book))
        if self.approvals_file is not None:
            write_approvals(self.approvals_file, self.shop, pending, self.clock())
        return Settlement(events=tuple(events), waiting=tuple(waiting),
                          needs_approval=tuple(pending))

    def answers_from_chain(
        self, decisions: list[Decision], book: PaymentBook
    ) -> list[OwnerAnswer]:
        """Wallet questions the owner answered by approving the wallet in the contract.

        The approval counts only when it was made after the agent first saw this wallet on the
        supplier (its first signature challenge for it), whether before or after the supplier
        signed. An approval made earlier, for example of an address that was another
        supplier's, answers nothing: the owner is asked once.
        """
        answers = []
        for decision in decisions:
            if not asks_only_about_wallet(decision):
                continue
            wallet = self.ledger.get_supplier(decision.supplier).wallet_address or ""
            if not is_evm_address(wallet):
                continue
            payee = to_checksum_address(wallet)
            noticed = book.wallet_noticed_at(decision.supplier, wallet)
            if noticed is None or not self.chain.is_approved(self.shop, payee):
                continue
            approved = self.chain.payee_approved_since(self.shop, payee, noticed)
            if approved is None:
                continue
            answers.append(OwnerAnswer(
                invoice=decision.invoice, fingerprint=fingerprint(decision),
                verdict=Verdict.APPROVE, answered_by=CHAIN_APPROVER, answered_at=self.clock(),
                note=f"wallet {payee} approved in the shop contract on "
                     f"{approved:%Y-%m-%d %H:%M} UTC",
            ))
        return answers

    def _list_for_approval(
        self, decision: Decision, book: PaymentBook, pending: list[PendingApproval]
    ) -> list[PaymentEvent]:
        wallet = self.ledger.get_supplier(decision.supplier).wallet_address or ""
        if not is_evm_address(wallet):
            return []
        payee = to_checksum_address(wallet)
        if self.chain.is_approved(self.shop, payee):
            return []  # approved already: answers_from_chain says whether that answers it
        pending.append(PendingApproval(wallet=payee, invoice=decision.invoice,
                                       amount=decision.amount))
        reason = answer_by_approval_reason(payee)
        last = book.latest_payment(decision.invoice)
        if last and last.status is PaymentStatus.BLOCKED and last.reason == reason:
            return []
        event = PaymentEvent(
            invoice=decision.invoice, status=PaymentStatus.BLOCKED, at=self.clock(),
            supplier=decision.supplier, payee=payee, amount=decision.amount,
            invoice_ref="0x" + invoice_ref(decision.invoice, self.ref_scope).hex(), reason=reason,
            **_converted(decision),
        )
        book.record_payment(event)
        return [event]

    def _record(self, done: PaymentEvent, book: PaymentBook) -> list[PaymentEvent]:
        if self.writer is None or not done.tx_hash:
            return []
        note = f"PAY decided by the embco agent; invoice ref {done.invoice_ref}"
        if done.circle_tx_id:
            note += f"; Circle transaction {done.circle_tx_id}"
        try:
            entry = self.writer.record_payment(SettledPayment(
                invoice=done.invoice, supplier=done.supplier, amount=done.amount,
                paid_on=done.at.date(), tx_hash=done.tx_hash, payee_wallet=done.payee,
                note=note, invoice_amount=done.invoice_amount, rate=done.rate,
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
        pending: list[PendingApproval],
        session: str | None = None,
        why: str = "",
    ) -> list[PaymentEvent] | None:
        name = decision.invoice
        ref = invoice_ref(name, self.ref_scope)
        base = PaymentEvent(
            invoice=name, status=PaymentStatus.BLOCKED, at=self.clock(),
            supplier=decision.supplier, payee="", amount=decision.amount,
            invoice_ref="0x" + ref.hex(), session=session, **_converted(decision),
        )

        def blocked(reason: str) -> list[PaymentEvent]:
            if last and last.status is PaymentStatus.BLOCKED and last.reason == reason:
                return []  # same reason as last time: nothing new to remember
            event = replace(base, reason=reason)
            book.record_payment(event)
            log.warning("payment of %s blocked: %s", name, reason)
            return [event]

        invoice = self.ledger.get_purchase_invoice(name)
        today = self.clock().astimezone(UTC).date()
        if invoice.docstatus != SUBMITTED_DOC:
            return blocked("the invoice is no longer submitted in the ERP (cancelled or a draft)")
        if invoice.supplier != decision.supplier:
            return blocked("the invoice's supplier changed since the decision; deciding again "
                           "next cycle")
        if invoice.held(today):
            return blocked("the invoice is on hold in the ERP")
        if invoice.currency != decision.currency:
            return blocked("the invoice's currency changed since the decision; deciding again "
                           "next cycle")
        if invoice.outstanding_amount != decision.billed:
            return blocked("the amount due changed since the decision; deciding again next cycle")
        if decision.currency != PAYABLE_CURRENCY:
            if decision.rate is None or decision.amount <= 0:
                return blocked(f"no exchange rate for {decision.currency}")
            if self.writer is not None and (
                    problem := self.writer.payment_account_problem(decision.currency)):
                return blocked(problem)
        supplier = self.ledger.get_supplier(decision.supplier)
        if supplier.payments_held(today):
            return blocked("payments to the supplier are on hold in the ERP")
        wallet = supplier.wallet_address or ""
        if not is_evm_address(wallet):
            return blocked("the supplier has no valid wallet on file")
        payee = to_checksum_address(wallet)
        base = replace(base, payee=payee)
        try:
            units = usdc_units(decision.amount)
        except EncodingError as error:
            return blocked(str(error))
        if self.chain.is_paid(self.shop, ref):
            if unanswered(last) and (tx_hash := self._lost_tx(last)):
                return [self._found(last, tx_hash, book)]  # its Circle answer was lost
            if book.payment_attempts(name) == 0:
                return blocked(FOREIGN_REF)  # never mark paid what the agent did not pay
            event = replace(base, status=PaymentStatus.COMPLETE,
                            reason="the contract already marks this invoice paid")
            book.record_payment(event)
            return [event]
        if not self.chain.is_approved(self.shop, payee):
            pending.append(PendingApproval(wallet=payee, invoice=name, amount=decision.amount))
            return blocked(approve_wallet_reason(payee))
        if units > remaining:
            return None
        data = pay_call(payee, units, ref)
        try:
            self.chain.call(self.shop, data, sender=agent)
        except Reverted as error:
            return blocked(f"the contract would refuse it: {error.reason}")
        attempt = book.payment_attempts(name)
        if attempt >= MAX_ATTEMPTS:
            return blocked(f"{attempt} transactions for this invoice failed; check it by hand")
        asking = replace(base, status=PaymentStatus.SUBMITTED, at=self.clock(), attempt=attempt,
                         why=why)
        book.record_payment(asking)  # first: a lost answer or a restart still leaves the payment
        try:
            tx = self.circle.execute_contract(
                self.wallet_id, self.shop, data,
                idempotency_key=idempotency_key(self.shop, ref, payee, units, attempt),
                ref_id=name,
            )
        except CircleUnanswered as error:
            log.warning("payment of %s: Circle's answer was lost (%s); the contract is checked "
                        "next, and a retry repeats the same request", name, error)
            return [asking]
        except CircleError as error:
            book.record_payment(replace(base, at=self.clock(), reason=str(error)))
            raise
        sent = replace(asking, at=self.clock(), circle_tx_id=tx.id, why="")
        book.record_payment(sent)
        log.info("payment of %s sent: %s USDC to %s (Circle %s)", name, decision.amount, payee,
                 tx.id)
        return [sent, *self._follow(sent, book, wait=True)]

    def _lost_tx(self, asking: PaymentEvent) -> str | None:
        """The transaction in which the agent's wallet paid an invoice whose Circle answer was
        lost, from the contract's Paid event; None when the agent did not pay it."""
        return self.chain.payment_tx(self.shop, bytes.fromhex(asking.invoice_ref[2:]),
                                     self.agent_address(), asking.at - CLOCK_SKEW)

    def _found(self, asking: PaymentEvent, tx_hash: str, book: PaymentBook) -> PaymentEvent:
        event = replace(asking, status=PaymentStatus.COMPLETE, at=self.clock(), tx_hash=tx_hash,
                        reason="", why="")
        book.record_payment(event)
        log.info("payment of %s found on Arc after its Circle answer was lost: %s",
                 asking.invoice, tx_hash)
        return event

    def _recover(self, asking: PaymentEvent, book: PaymentBook) -> list[PaymentEvent]:
        """Every cycle, for a payment whose Circle answer was lost: once it is on Arc it is
        written as paid. Nothing is sent here."""
        try:
            tx_hash = self._lost_tx(asking)
        except (ChainError, CircleError, PaymentSetupError) as error:
            log.warning("could not check the lost payment of %s: %s", asking.invoice, error)
            return []
        return [self._found(asking, tx_hash, book)] if tx_hash else []

    def _follow(self, sent: PaymentEvent, book: PaymentBook, *, wait: bool) -> list[PaymentEvent]:
        if not sent.circle_tx_id:
            return self._recover(sent, book) if unanswered(sent) else []
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
        if settled_or_sent(book.latest_payment(d.invoice), book):
            result.add(d.invoice)
    return result

