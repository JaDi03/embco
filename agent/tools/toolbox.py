"""The agent's hands: what each tool reads or does during one session.

Reading tools look at the ERP, the journal, the contract and the agent's own wallet. pay_now
acts for real: it passes the guardrail gate, then the payer sends the payment and the agent gets
back what happened, refusals and errors included, word for word. The other choices (schedule,
hold, ask) count once the session finishes.
"""

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import Any

from agent.guardrails.controls.base import Outcome, fmt, short
from agent.guardrails.controls.payee_wallet import WAITING_FOR_SIGNATURE
from agent.guardrails.gate import pay_refusal, schedule_refusal
from agent.guardrails.rules import Action, Decision, PolicyConfig, fingerprint
from agent.memory import DecisionJournal
from agent.models import AgentDecision, Alarm, Choice, Note
from agent.tools.definitions import TOOLS
from services.circle import CircleError
from services.erp import LedgerAdapter, LedgerError
from services.payments import ChainError, PaymentEvent, PaymentStatus
from services.payments.payer import PaymentRefused, PaymentSetupError
from services.unified_balance import Allocation, UnifiedBalanceError

MAX_ALARMS = 5
MAX_NOTES = 10
MAX_NOTE_CHARS = 600
MAX_ALARM_DAYS = 60
MAX_EMAIL_CHARS = 1500  # an email to a supplier
MAX_REPLY_CHARS = 400  # an answer to the owner: one topic, a couple of sentences
MAX_NOTICE_CHARS = 280  # a notice the owner did not ask for: one thing, shorter still
HASHES = re.compile(r"0x[0-9a-fA-F]{40,}")  # full addresses and tx hashes: the dashboard has them
SEARCH_LIMIT = 50
MAX_NOTICES = 3
MAX_EMAILS_PER_DAY = 3
MAX_PAYMENTS = 10  # per session; the contract's weekly cap is the real limit
MAX_FUND_MOVES = 3  # bring_funds calls per session
FORWARDER = "forwarder"  # the fee taken from what is minted on Arc; the others come off the balance
LINKS = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(com|net|org|xyz|io|app|link)\b", re.I)
UNKNOWN_FUNDS = {"weekly_room_resets_at_utc": "unknown", "owner_balance": "unknown",
                 "payments_authorized": "unknown"}  # the chain did not answer: never a guess
RECENT = 10
INPUTS = {t["name"]: set(t["input_schema"]["properties"]) for t in TOOLS}


class ToolError(Exception):
    """A call the agent should correct: the message goes back to it as the tool's result."""


@dataclass
class Toolbox:
    decisions: list[Decision]  # the rule decisions of this cycle, owner answers applied
    ledger: LedgerAdapter
    journal: DecisionJournal
    policy: PolicyConfig
    now: datetime
    zone: tzinfo  # the shop's local time
    done: set[str] = field(default_factory=set)  # paid or sent by the agent already
    standing: Mapping[str, AgentDecision] = field(default_factory=dict)
    room: Callable[[], Decimal | None] = lambda: None  # what the contract allows this week
    funds: Callable[[], dict[str, str] | None] = lambda: None  # reset time, balance, authorization
    mailer: Callable[[str, str, str], str] | None = None  # writes to a supplier from the ERP
    wallet_notice: Callable[[str], str] | None = None  # the standard wallet notice, from the ERP
    emails_today: int = 0  # emails the agent already sent today, for the daily cap
    past_notes: tuple[Note, ...] = ()
    messages: tuple[str, ...] = ()  # what the owner wrote since the agent's last reply
    require_all: bool = True  # a decision round decides every open invoice; a chat does not
    payer: Any = None  # pays one invoice when the agent chooses to (services.payments.Payer)
    can_pay: bool = True  # False while the owner has paused payments
    payments: list[PaymentEvent] = field(default_factory=list)  # what the agent's payments did
    owner_funds: Any = None  # the owner's Unified Balance (services.unified_balance.OwnerFunds)
    funds_moved: list[dict[str, Any]] = field(default_factory=list)  # what bring_funds brought
    chosen: dict[str, AgentDecision] = field(default_factory=dict)
    alarms: list[Alarm] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)  # to the owner: answers and notices
    notices: int = 0
    emailed: list[str] = field(default_factory=list)
    notified_wallets: list[str] = field(default_factory=list)  # suppliers sent the wallet notice
    summary: str | None = None

    def __post_init__(self) -> None:
        self._by_invoice = {d.invoice: d for d in self.decisions}
        self._room: Decimal | None = None
        self._room_read = False
        self._paid_now = 0

    @property
    def today(self) -> date:
        return self.now.astimezone(self.zone).date()

    def call(self, name: str, args: Mapping[str, Any]) -> str:
        """Run one tool. Raises ToolError for a call the agent must correct."""
        if name not in INPUTS:
            raise ToolError(f"there is no tool called {name}")
        if set(args) != INPUTS[name] or not all(isinstance(v, str) for v in args.values()):
            raise ToolError(f"{name} takes these text inputs: {', '.join(sorted(INPUTS[name]))}")
        try:
            result = getattr(self, f"_{name}")(**args)
        except LedgerError as error:
            raise ToolError(f"the ERP did not answer: {error}") from None
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

    def current(self, invoice: str) -> AgentDecision | None:
        """The agent's decision that still holds: made in this session, or earlier on the same
        rule decision."""
        if invoice in self.chosen:
            return self.chosen[invoice]
        standing = self.standing.get(invoice)
        decision = self._by_invoice.get(invoice)
        if standing and decision and standing.fingerprint == fingerprint(decision):
            return standing
        return None

    def undecided(self) -> list[str]:
        return [d.invoice for d in self.decisions
                if d.invoice not in self.done and self.current(d.invoice) is None]

    def open_invoices(self) -> list[dict[str, Any]]:
        return [self._row(d) for d in self.decisions]

    # reading

    def _list_open_invoices(self) -> list[dict[str, Any]]:
        return self.open_invoices()

    def _search_invoices(self, supplier: str, due_from: str, due_to: str,
                         checks_say: str) -> dict[str, Any]:
        start = _parse_day(due_from) if due_from.strip() else None
        end = _parse_day(due_to) if due_to.strip() else None
        says = checks_say.strip().upper()
        if says and says not in {a.value for a in Action}:
            raise ToolError("checks_say is PAY, HOLD, ASK or empty")
        name = supplier.strip().lower()
        found = [d for d in self.decisions
                 if (not name or name in d.supplier.lower())
                 and (not says or d.action.value == says)
                 and (start is None or (d.due_date is not None and d.due_date >= start))
                 and (end is None or (d.due_date is not None and d.due_date <= end))]
        return {"count": len(found), "total": fmt(sum((d.amount for d in found), Decimal(0))),
                "invoices": [self._row(d) for d in found[:SEARCH_LIMIT]]}

    def _get_invoice(self, invoice: str) -> dict[str, Any]:
        decision = self._decision(invoice)
        doc = self.ledger.get_purchase_invoice(invoice)
        answer = self.journal.latest_answer(invoice)
        payment = self.journal.latest_payment(invoice)
        history = self.journal.history(invoice)[-5:]
        return {
            **self._row(decision),
            "bill_no": doc.bill_no,
            "posting_date": _day(doc.posting_date),
            "currency": doc.currency,
            "grand_total": fmt(doc.grand_total),
            "lines": [{"item_code": line.item_code, "qty": fmt(line.qty), "rate": fmt(line.rate),
                       "amount": fmt(line.amount), "purchase_order": line.purchase_order,
                       "purchase_receipt": line.purchase_receipt} for line in doc.lines],
            "checks": [{"check": f.control, "result": f.outcome.value, "detail": f.reason}
                       for f in decision.findings],
            "owner_answer": None if answer is None else {
                "verdict": answer.verdict.value, "by": answer.answered_by,
                "at": answer.answered_at.isoformat(), "note": answer.note},
            "payment": None if payment is None else {
                "status": payment.status.value, "reason": payment.reason,
                "tx": payment.tx_hash, "at": payment.at.isoformat()},
            "rule_history": [{"at": e.recorded_at.isoformat(), "checks_say": e.action.value,
                              "reasons": list(e.reasons)} for e in history],
        }

    def _supplier_profile(self, supplier: str) -> dict[str, Any]:
        info = self.ledger.get_supplier(supplier)
        proof = self.journal.latest_wallet_proof(supplier)
        wallet = info.wallet_address or ""
        invoices = self.ledger.list_supplier_invoices(supplier)[-RECENT:]
        payments = self.ledger.list_payments(supplier)[-RECENT:]
        return {
            "supplier": supplier,
            "disabled": info.disabled,
            "wallet": short(wallet) if wallet else None,
            "wallet_problem": info.wallet_problem,
            "wallet_proven_by_signature": bool(
                proof and wallet and proof.wallet.lower() == wallet.lower()),
            "recent_invoices": [{"invoice": i.name, "date": _day(i.posting_date),
                                 "total": fmt(i.grand_total),
                                 "outstanding": fmt(i.outstanding_amount)} for i in invoices],
            "recent_payments": [{"date": _day(p.posting_date), "amount": fmt(p.amount),
                                 "wallet": short(p.payee_wallet) if p.payee_wallet else None}
                                for p in payments],
            "your_notes": [n.text for n in self._notes_about(supplier)],
        }

    def _price_history(self, supplier: str, item_code: str) -> list[dict[str, Any]]:
        rows = []
        for doc in self.ledger.list_supplier_invoices(supplier):
            for line in doc.lines:
                if line.item_code == item_code:
                    rows.append({"invoice": doc.name, "date": _day(doc.posting_date),
                                 "qty": fmt(line.qty), "rate": fmt(line.rate)})
        return rows[-20:]

    def _cash_position(self) -> dict[str, Any]:
        room = self._room_this_week()
        soon = self.today + timedelta(days=7)
        due_soon = [d for d in self.decisions if d.action is Action.PAY
                    and d.invoice not in self.done and d.due_date and d.due_date <= soon]
        scheduled = [(i, c) for i in self._by_invoice
                     if (c := self.current(i)) and c.choice is Choice.SCHEDULE]
        return {
            "max_per_payment": fmt(self.policy.max_per_payment),
            "weekly_cap": fmt(self.policy.weekly_budget),
            "room_this_week": fmt(room) if room is not None else "unknown",
            "chosen_to_pay_now": fmt(self._committed()),
            "scheduled": [{"invoice": i, "pay_on": _day(c.pay_on),
                           "amount": fmt(self._by_invoice[i].amount)} for i, c in scheduled],
            "payable_due_within_7_days": fmt(sum((d.amount for d in due_soon), Decimal(0))),
            "today": self.today.isoformat(),
            **(self._funds_once() or UNKNOWN_FUNDS),
            "usdc_on_other_chains": self._other_chains(),
            "usdc_in_owner_wallets_on_other_chains": self._owner_wallets(),
        }

    def _owner_wallets(self) -> list[dict[str, str]] | str:
        """The owner's own wallets on other chains, and how much the agent may deposit."""
        if self.owner_funds is None:
            return "not connected for this shop"
        try:
            wallets = self.owner_funds.in_wallets()
        except (ChainError, CircleError, PaymentSetupError, UnifiedBalanceError) as error:
            return f"unknown: {error}"
        rows = [{"chain": w.chain, "in_wallet": fmt(w.in_wallet),
                 "you_may_deposit_now": fmt(w.can_move) if w.funding
                 else "no: the owner sets up this chain in the dashboard"}
                for w in wallets]
        return rows or "no other chain is set up"

    def _other_chains(self) -> list[dict[str, str]] | str:
        """The owner's Unified Balance, chain by chain, and where the agent may spend it."""
        if self.owner_funds is None:
            return "not connected for this shop"
        try:
            chains = self.owner_funds.chains()
        except (UnifiedBalanceError, ChainError, CircleError, PaymentSetupError) as error:
            return f"unknown: {error}"
        rows = [{"chain": c.chain, "available": fmt(c.confirmed), "arriving": fmt(c.pending),
                 "you_may_spend_it": "yes" if c.agent_can_spend
                 else "no: the owner authorizes you on this chain in the dashboard"}
                for c in chains if c.confirmed or c.pending or c.agent_can_spend]
        return rows or "the owner has no USDC on other chains"

    # acting

    def _pay_now(self, invoice: str, reason: str) -> str | dict[str, Any]:
        decision = self._by_invoice.get(invoice)
        committed = self._committed(excluding=invoice)
        refusal = pay_refusal(decision, done=self.done, room=self._room_this_week(),
                              committed=committed)
        if refusal:
            raise ToolError(f"not allowed: {refusal}")
        if self.payer is None:
            self._choose(decision, Choice.PAY_NOW, reason)
            return (f"{invoice}: payments are not connected for this shop; your decision is "
                    "recorded and nothing was sent")
        if not self.can_pay:
            self._choose(decision, Choice.PAY_NOW, reason)
            return (f"{invoice}: payments are paused by the owner; your decision is recorded and "
                    "nothing was sent. Say in your summary what you would pay")
        if self._paid_now >= MAX_PAYMENTS:
            raise ToolError(f"at most {MAX_PAYMENTS} payments per session; look again later")
        _refuse_workarounds(reason)
        self._paid_now += 1
        try:
            events = self.payer.pay(decision, self.journal)
        except PaymentRefused as error:
            self.payments.append(self._not_sent(decision, str(error)))
            raise ToolError(f"not paid, nothing was sent: {error}") from None
        self.payments.extend(events)
        last = events[-1]
        if last.status is PaymentStatus.FAILED:
            raise ToolError(f"the payment was sent but failed: {last.reason}")
        self._choose(decision, Choice.PAY_NOW, reason)
        self.done.add(invoice)
        return _payment_view(events)

    def _payment_status(self, invoice: str) -> dict[str, Any]:
        if self.payer is None:
            raise ToolError("payments are not connected for this shop")
        try:
            events = self.payer.status(invoice, self.journal)
        except (ChainError, CircleError) as error:
            raise ToolError(f"could not look it up: {error}") from None
        if not events:
            return {"invoice": invoice, "status": "no payment by the agent"}
        self.payments.extend(e for e in events[1:])
        return _payment_view(events)

    def _agent_wallet(self) -> dict[str, Any]:
        if self.payer is None:
            raise ToolError("payments are not connected for this shop")
        try:
            return self.payer.wallet_info()
        except (ChainError, CircleError) as error:
            raise ToolError(f"could not read the agent's wallet: {error}") from None

    def _deposit_to_gateway(self, chain: str, amount: str, reason: str) -> dict[str, Any]:
        if self.owner_funds is None:
            raise ToolError("the owner's wallets on other chains are not connected")
        value = _decimal(amount)
        if value is None or value <= 0:
            raise ToolError("amount is USDC above 0, e.g. 120 or 30.5")
        if not self.can_pay:
            raise ToolError("payments are paused by the owner; nothing was moved")
        if len(self.funds_moved) >= MAX_FUND_MOVES:
            raise ToolError(f"at most {MAX_FUND_MOVES} fund moves per session; look again later")
        try:
            done = self.owner_funds.deposit(chain.strip(), value)
        except (ChainError, CircleError, PaymentSetupError, UnifiedBalanceError,
                ValueError) as error:
            raise ToolError(f"nothing was deposited: {error}") from None
        moved = {"kind": "deposit", "chain": chain.strip(), "amount": fmt(value),
                 "state": done["state"], "tx": done["tx"], "reason": reason.strip()}
        self.funds_moved.append(moved)
        return moved

    def _estimate_bring_funds(self, allocations: str) -> dict[str, Any]:
        split = self._allocations(allocations)
        try:
            estimate = self.owner_funds.estimate(split)
        except (UnifiedBalanceError, ChainError, CircleError, PaymentSetupError) as error:
            raise ToolError(f"could not estimate it: {error}") from None
        return _moved(split, estimate.get("fees", []))

    def _bring_funds(self, allocations: str, reason: str) -> dict[str, Any]:
        split = self._allocations(allocations)
        if not self.can_pay:
            raise ToolError("payments are paused by the owner; nothing was moved")
        if len(self.funds_moved) >= MAX_FUND_MOVES:
            raise ToolError(f"at most {MAX_FUND_MOVES} fund moves per session; look again later")
        try:
            result = self.owner_funds.bring(split)
        except (UnifiedBalanceError, ChainError, CircleError, PaymentSetupError) as error:
            raise ToolError(f"nothing was brought: {error}") from None
        moved = {**_moved(split, result.get("fees", [])), "reason": reason.strip(),
                 "tx": result.get("txHash") or result.get("transferId") or ""}
        self.funds_moved.append(moved)
        if hasattr(self, "_funds"):
            del self._funds  # the owner's balance on Arc changed: read it again when asked
        return moved

    def _allocations(self, text: str) -> list[Allocation]:
        """chain:amount pairs, each chain once, each amount positive."""
        if self.owner_funds is None:
            raise ToolError("the owner's Unified Balance is not connected for this shop")
        split: dict[str, Decimal] = {}
        for part in filter(None, (p.strip() for p in text.split(","))):
            chain, _, amount = part.partition(":")
            chain, value = chain.strip(), _decimal(amount)
            if not chain or value is None or value <= 0 or chain in split:
                raise ToolError("allocations are chain:amount pairs, each chain once, each "
                                "amount above 0, e.g. Base_Sepolia:120, Arbitrum_Sepolia:30.5")
            split[chain] = value
        if not split:
            raise ToolError("say how much from which chain, e.g. Base_Sepolia:120")
        return [Allocation(chain, amount) for chain, amount in split.items()]

    def _schedule_payment(self, invoice: str, pay_on: str, reason: str) -> str:
        decision = self._by_invoice.get(invoice)
        day = _parse_day(pay_on)
        refusal = schedule_refusal(decision, day, self.today, done=self.done,
                                   room=self._room_this_week(), renews_on=self._renews_on())
        if refusal:
            raise ToolError(f"not allowed: {refusal}")
        if decision.due_date and day > decision.due_date and not self.messages:
            raise ToolError(
                f"not allowed: {day} is after its due date ({decision.due_date}); paying late is "
                "the owner's call. Ask with ask_owner: the options (pay on the first day it "
                "fits, the owner raises the weekly budget or adds funds, you write to the "
                "supplier) and what you recommend")
        self._choose(decision, Choice.SCHEDULE, reason, pay_on=day)
        late = decision.due_date and day > decision.due_date
        return f"{invoice}: scheduled for {day}" + (
            f" (after its due date {decision.due_date})" if late else "")

    def _hold(self, invoice: str, reason: str) -> str:
        self._choose(self._decision(invoice), Choice.HOLD, reason)
        return f"{invoice}: held"

    def _ask_owner(self, invoice: str, question: str, recommendation: str) -> str:
        decision = self._decision(invoice)
        if not question.strip():
            raise ToolError("the question is empty")
        self._choose(decision, Choice.ASK, question, question=question.strip(),
                     recommendation=recommendation.strip())
        return f"{invoice}: the owner will be asked"

    def _set_alarm(self, at: str, why: str, invoice: str = "") -> str:
        if len(self.alarms) >= MAX_ALARMS:
            raise ToolError(f"at most {MAX_ALARMS} alarms per session")
        try:
            when = datetime.fromisoformat(at)
        except ValueError:
            raise ToolError("use the shop's local time as YYYY-MM-DDTHH:MM") from None
        when = when.replace(tzinfo=self.zone) if when.tzinfo is None else when
        if when <= self.now:
            raise ToolError("that time has already passed")
        if when > self.now + timedelta(days=MAX_ALARM_DAYS):
            raise ToolError(f"set alarms at most {MAX_ALARM_DAYS} days ahead")
        invoice = invoice.strip()
        if invoice and invoice not in {d.invoice for d in self.decisions}:
            raise ToolError(f"{invoice} is not an open invoice; leave it empty or use the name "
                            "exactly as listed")
        self.alarms.append(Alarm(at=when, why=why.strip(), set_at=self.now,
                                 invoice=invoice or None))
        return f"alarm set for {when.astimezone(self.zone):%Y-%m-%d %H:%M}"

    def _note(self, about: str, text: str) -> str:
        if len(self.notes) >= MAX_NOTES:
            raise ToolError(f"at most {MAX_NOTES} notes per session")
        self.notes.append(Note(about=about.strip(), text=text.strip()[:MAX_NOTE_CHARS],
                               at=self.now))
        return "noted"

    def _email_supplier(self, supplier: str, subject: str, body: str) -> str:
        if self.mailer is None:
            raise ToolError("email is not available for this shop")
        if not self.messages:
            raise ToolError("write to a supplier only after the owner agreed in this "
                            "conversation; ask the owner first")
        if supplier not in {d.supplier for d in self.decisions}:
            raise ToolError(f"{supplier} has no open invoice")
        if self.emails_today + len(self.emailed) >= MAX_EMAILS_PER_DAY:
            raise ToolError(f"at most {MAX_EMAILS_PER_DAY} emails to suppliers per day")
        text = f"{subject}\n{body}"
        if LINKS.search(text):
            raise ToolError("no links in an email to a supplier")
        if not subject.strip() or not body.strip():
            raise ToolError("the subject and the message must not be empty")
        _refuse_workarounds(text)
        try:
            address = self.mailer(supplier, subject.strip()[:120], body.strip()[:MAX_EMAIL_CHARS])
        except LedgerError as error:
            raise ToolError(f"the email was not sent: {error}") from None
        self.emailed.append(supplier)
        return f"sent to {supplier} at {address}"

    def _ask_supplier_to_confirm_wallet(self, supplier: str) -> str:
        if self.wallet_notice is None:
            raise ToolError("email is not available for this shop")
        if not self.messages:
            raise ToolError("send the wallet notice only after the owner agreed in this "
                            "conversation; ask the owner first")
        waiting = {d.supplier for d in self.decisions for f in d.findings
                   if f.outcome is not Outcome.PASS and WAITING_FOR_SIGNATURE in f.reason}
        if supplier not in waiting:
            raise ToolError(f"{supplier} has no wallet waiting for its signature")
        if supplier in self.notified_wallets:
            raise ToolError(f"the notice already went to {supplier} in this session")
        try:
            address = self.wallet_notice(supplier)
        except LedgerError as error:
            raise ToolError(f"the notice was not sent: {error}") from None
        self.notified_wallets.append(supplier)
        return f"notice sent to {supplier} at {address}"

    def _notify_owner(self, text: str) -> str:
        if self.notices >= MAX_NOTICES:
            raise ToolError(f"at most {MAX_NOTICES} notices per session")
        if not text.strip():
            raise ToolError("the notice is empty")
        _refuse_workarounds(text)
        _keep_it_short(text, MAX_NOTICE_CHARS)
        self.replies.append(text.strip())
        self.notices += 1
        return "the owner will see it on the dashboard and on their phone"

    def _reply_owner(self, text: str) -> str:
        if not self.messages:
            raise ToolError("the owner did not write; put what you have to say in the summary")
        if not text.strip():
            raise ToolError("the answer is empty")
        _refuse_workarounds(text)
        _keep_it_short(text, MAX_REPLY_CHARS)
        self.replies.append(text.strip())
        return "sent to the owner"

    def _finish(self, summary: str) -> str:
        missing = self.undecided() if self.require_all else []
        if missing:
            raise ToolError("decide on these invoices first: " + ", ".join(missing))
        if self.messages and not self.replies:
            raise ToolError("answer the owner's message with reply_owner first")
        if not summary.strip():
            raise ToolError("the summary for the owner is empty")
        _refuse_workarounds(summary)
        self.summary = summary.strip()
        return "session finished"

    # helpers

    def _decision(self, invoice: str) -> Decision:
        decision = self._by_invoice.get(invoice)
        if decision is None:
            raise ToolError(f"{invoice} is not among the unpaid invoices")
        return decision

    def _choose(self, decision: Decision, choice: Choice, reason: str, **extra: Any) -> None:
        if not reason.strip():
            raise ToolError("give a reason the owner can read")
        _refuse_workarounds(reason, *(v for v in extra.values() if isinstance(v, str)))
        self.chosen[decision.invoice] = AgentDecision(
            invoice=decision.invoice, fingerprint=fingerprint(decision), choice=choice,
            reason=reason.strip(), decided_at=self.now, **extra)

    def _committed(self, excluding: str = "") -> Decimal:
        return sum((self._by_invoice[i].amount for i, c in self.chosen.items()
                    if c.choice is Choice.PAY_NOW and i != excluding), Decimal(0))

    def _renews_on(self) -> date | None:
        """The shop's local day the contract's week renews, from the chain; None if unknown."""
        text = (self._funds_once() or {}).get("weekly_room_resets_at_utc", "")
        try:
            at = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        except ValueError:
            return None
        return at.astimezone(self.zone).date()

    def _funds_once(self) -> dict[str, str] | None:
        if not hasattr(self, "_funds"):
            self._funds = self.funds()
        return self._funds

    def _room_this_week(self) -> Decimal | None:
        if not self._room_read:
            self._room, self._room_read = self.room(), True
        return self._room

    def _last_payment(self, invoice: str) -> dict[str, Any] | None:
        last = self.journal.latest_payment(invoice)
        return None if last is None else {"status": _STATUS.get(last.status, last.status.value),
                                          "reason": last.reason or None,
                                          "at": last.at.isoformat(timespec="minutes")}

    def _not_sent(self, decision: Decision, reason: str) -> PaymentEvent:
        """A payment that did not go out, for the owner's feed (the journal keeps only the
        ones the payer recorded)."""
        return PaymentEvent(invoice=decision.invoice, status=PaymentStatus.BLOCKED, at=self.now,
                            supplier=decision.supplier, payee="", amount=decision.amount,
                            invoice_ref="", reason=reason)

    def _notes_about(self, about: str) -> list[Note]:
        return [n for n in (*self.past_notes, *self.notes) if n.about == about]

    def _row(self, d: Decision) -> dict[str, Any]:
        mine = self.current(d.invoice)
        return {
            "invoice": d.invoice,
            "supplier": d.supplier,
            "amount": fmt(d.amount),
            "due_date": _day(d.due_date),
            "checks_say": d.action.value,
            "stops": [] if d.action is Action.PAY else [  # an answered question no longer stops
                f.reason for f in d.findings if f.outcome in (Outcome.HOLD, Outcome.ASK)],
            "to_weigh": [f.reason for f in d.findings if f.outcome is Outcome.NOTE],
            "owner_answer": next((r for r in d.reasons if r.startswith(("approved", "rejected"))),
                                 None),
            "paid_or_sent": d.invoice in self.done,
            "last_payment": self._last_payment(d.invoice),
            "your_decision": None if mine is None else {
                "choice": mine.choice.value, "reason": mine.reason,
                "pay_on": _day(mine.pay_on)},
        }


WORKAROUNDS = re.compile(
    r"\b(split\w*|divid\w*|in (two|several|smaller) (parts|payments)|instal+ments?|"
    r"(raise|increase|lift|higher) (the |your )?(per-payment |weekly )?(limit|cap)|"
    r"(another|other) way|outside (the|this) contract)\b", re.IGNORECASE)


def _refuse_workarounds(*texts: str) -> None:
    """The owner never reads a way around a check or a limit from the agent, even if the model
    writes one: the text goes back to the model to be rewritten."""
    for text in texts:
        if found := WORKAROUNDS.search(text):
            raise ToolError(f"rewrite this without suggesting a way around a limit or a check "
                            f"(found: {found.group(0)!r}); an invoice over a limit waits")


def _keep_it_short(text: str, limit: int) -> None:
    """What reaches the owner's chat and phone is one topic in a few words: a longer text, or
    one with a full address or transaction hash, goes back to the model to be rewritten."""
    if HASHES.search(text):
        raise ToolError("rewrite this without full addresses or transaction hashes; the "
                        "dashboard links them")
    if len(text.strip()) > limit:
        raise ToolError(f"too long ({len(text.strip())} characters, at most {limit}): say only "
                        "what this message is about, in one or two short sentences; other "
                        "invoices and details are in the dashboard")


_STATUS = {
    PaymentStatus.SUBMITTED: "sent, not confirmed yet: look again with payment_status",
    PaymentStatus.COMPLETE: "paid on Arc",
    PaymentStatus.RECORDED: "paid on Arc and recorded in the ERP",
    PaymentStatus.FAILED: "failed",
    PaymentStatus.BLOCKED: "not sent",
}


def _payment_view(events: list[PaymentEvent]) -> dict[str, Any]:
    """What the agent sees of a payment: its latest state and the facts behind it."""
    last = events[-1]
    tx = next((e.tx_hash for e in reversed(events) if e.tx_hash), None)
    entry = next((e.erp_entry for e in reversed(events) if e.erp_entry), None)
    return {"invoice": last.invoice, "status": _STATUS.get(last.status, last.status.value),
            "amount": fmt(last.amount), "to_wallet": short(last.payee) if last.payee else None,
            "transaction": tx, "erp_entry": entry, "reason": last.reason or None}


def _day(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _parse_day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ToolError("use a date as YYYY-MM-DD") from None


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.strip())
    except (ArithmeticError, ValueError):
        return None


def _moved(split: list[Allocation], fees: list[dict[str, Any]]) -> dict[str, Any]:
    """What a move takes from each chain, its fees and what reaches the owner's wallet on Arc.
    Each chain's gas and protocol fee come out of that chain's balance on top of the amount (the
    forwarder's fee comes off what is minted on Arc), so a chain gives up more than its amount."""
    total = sum((a.amount for a in split), Decimal(0))
    fee = {f.get("type", ""): Decimal(str(f.get("amount", "0"))) for f in fees}
    on_top: dict[str, Decimal] = {}
    for f in fees:
        if f.get("type") == FORWARDER:
            continue
        for part in f.get("allocations") or []:
            chain = part.get("chain", "")
            on_top[chain] = on_top.get(chain, Decimal(0)) + Decimal(str(part.get("amount", "0")))
    return {"from": [{"chain": a.chain, "amount": fmt(a.amount),
                      "taken_from_balance": fmt(a.amount + on_top.get(a.chain, Decimal(0)))}
                     for a in split],
            "total": fmt(total), "fees": {k: fmt(v) for k, v in fee.items()},
            "arrives_on_arc": fmt(total - fee.get(FORWARDER, Decimal(0)))}
