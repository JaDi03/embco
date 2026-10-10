"""The agent's hands: what each tool reads or does during one session.

Reading tools look at the ERP, the journal and the contract. Acting tools only stage the agent's
choices; the cycle applies them after the session, and only if the session finishes. Every
payment choice passes the guardrail gate first, and its refusal goes back to the agent.
"""

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import Any

from agent.guardrails.controls.base import Outcome, fmt, short
from agent.guardrails.gate import pay_refusal, schedule_refusal
from agent.guardrails.rules import Action, Decision, PolicyConfig, fingerprint
from agent.memory import DecisionJournal
from agent.models import AgentDecision, Alarm, Choice, Note
from agent.tools.definitions import TOOLS
from services.erp import LedgerAdapter, LedgerError

MAX_ALARMS = 5
MAX_NOTES = 10
MAX_NOTE_CHARS = 600
MAX_ALARM_DAYS = 60
MAX_REPLY_CHARS = 1500
SEARCH_LIMIT = 50
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
    past_notes: tuple[Note, ...] = ()
    messages: tuple[str, ...] = ()  # what the owner wrote since the agent's last reply
    require_all: bool = True  # a decision round decides every open invoice; a chat does not
    chosen: dict[str, AgentDecision] = field(default_factory=dict)
    alarms: list[Alarm] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    summary: str | None = None

    def __post_init__(self) -> None:
        self._by_invoice = {d.invoice: d for d in self.decisions}
        self._room: Decimal | None = None
        self._room_read = False

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
        }

    # acting

    def _pay_now(self, invoice: str, reason: str) -> str:
        decision = self._by_invoice.get(invoice)
        committed = self._committed(excluding=invoice)
        refusal = pay_refusal(decision, done=self.done, room=self._room_this_week(),
                              committed=committed)
        if refusal:
            raise ToolError(f"not allowed: {refusal}")
        self._choose(decision, Choice.PAY_NOW, reason)
        return (f"{invoice}: will be paid at the end of this session "
                f"({fmt(decision.amount)} USDC); the contract checks it again")

    def _schedule_payment(self, invoice: str, pay_on: str, reason: str) -> str:
        decision = self._by_invoice.get(invoice)
        day = _parse_day(pay_on)
        refusal = schedule_refusal(decision, day, self.today, done=self.done,
                                   room=self._room_this_week(), renews_on=self._renews_on())
        if refusal:
            raise ToolError(f"not allowed: {refusal}")
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

    def _set_alarm(self, at: str, why: str) -> str:
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
        self.alarms.append(Alarm(at=when, why=why.strip(), set_at=self.now))
        return f"alarm set for {when.astimezone(self.zone):%Y-%m-%d %H:%M}"

    def _note(self, about: str, text: str) -> str:
        if len(self.notes) >= MAX_NOTES:
            raise ToolError(f"at most {MAX_NOTES} notes per session")
        self.notes.append(Note(about=about.strip(), text=text.strip()[:MAX_NOTE_CHARS],
                               at=self.now))
        return "noted"

    def _reply_owner(self, text: str) -> str:
        if not self.messages:
            raise ToolError("the owner did not write; put what you have to say in the summary")
        if not text.strip():
            raise ToolError("the answer is empty")
        _refuse_workarounds(text)
        self.replies.append(text.strip()[:MAX_REPLY_CHARS])
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
            "problems": [f.reason for f in d.findings if f.outcome is not Outcome.PASS]
            or ([r for r in d.reasons if r.startswith(("approved", "rejected"))]),
            "paid_or_sent": d.invoice in self.done,
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


def _day(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _parse_day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ToolError("use a date as YYYY-MM-DD") from None
