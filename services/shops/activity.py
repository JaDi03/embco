"""What the shop's agent does, as a feed the owner watches live on the dashboard.

Each cycle appends short events to `<shop>/activity.jsonl`: the check starting and ending, new
and changed decisions, payments with their transaction, supplier signatures, the owner's
answers and errors. Each event has a sequence number so the dashboard asks only for new ones.
Only the last MAX_EVENTS are kept. The text is for the owner: plain words, no internals.
"""

import json
import os
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from agent.cost import estimate, shown
from agent.guardrails.controls.base import fmt
from agent.guardrails.rules import Action, PolicyConfig
from agent.memory import ChangeKind
from agent.reflexes.cycle import CycleReport
from agent.tools import trace as tool_trace
from services.payments import PaymentStatus

ACTIVITY = "activity.jsonl"
MAX_EVENTS = 1000

CHECK, DONE, NEW, CHANGED, CLOSED = "check", "done", "new", "changed", "closed"
SENDING, PAID, RECORDED, NOT_PAID = "sending", "paid", "recorded", "not_paid"
SIGNATURE, ANSWER, LIMITS, ERROR, WAITING = "signature", "answer", "limits", "error", "waiting"
AGENT, REFLEX = "agent", "reflex"  # what the agent decided; what woke it
TOOL = "tool"  # one tool the model called in a session, with what came back
YOU = "you"  # what the owner did from the dashboard
SHOWN_WAKES = 3

_SENT = (PaymentStatus.SUBMITTED, PaymentStatus.COMPLETE, PaymentStatus.RECORDED)
_ACTION = {Action.PAY: "nothing stops it", Action.HOLD: "on hold", Action.ASK: "needs you"}


def _why(action: Action, reasons) -> str:
    """What stops it, or the first thing the agent has to weigh: an owner's answer comes first
    in the reasons, so the first thing to weigh is looked for."""
    if action is not Action.PAY:
        return f": {reasons[0]}" if reasons else ""
    weigh = next((r for r in reasons if r.startswith("to weigh")), None)
    return f"; {weigh}" if weigh else ""


def _changed(invoice: str, supplier: str, action: Action, reasons) -> str:
    return f"{invoice} from {supplier}: now {_ACTION[action]}{_why(action, reasons)}."


def _short(wallet: str) -> str:
    return f"{wallet[:6]}...{wallet[-4:]}" if wallet and len(wallet) > 12 else wallet or ""


def _event(at: datetime, kind: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"at": at.isoformat(), "kind": kind, "text": text,
            **{k: v for k, v in extra.items() if v}}


def switched(at: datetime, on: bool) -> dict[str, Any]:
    if on:
        return _event(at, YOU, "You turned the agent on. It checks your ERPNext now.")
    return _event(at, YOU, "You turned the agent off. Nothing runs until you turn it on: no "
                           "checks, no Claude, no payments.")


def draft_mode_switched(at: datetime, draft: bool) -> dict[str, Any]:
    if draft:
        return _event(at, YOU, "You turned draft mode on: each payment stays in ERPNext as a "
                               "draft marked TESTNET and closes no invoice.")
    return _event(at, YOU, "You turned draft mode off: each payment is now submitted in ERPNext "
                           "and closes its invoice, even when it was paid in test USDC.")


def check_started(at: datetime) -> dict[str, Any]:
    return _event(at, CHECK, "Checking your ERPNext for unpaid invoices...")


def check_failed(at: datetime, error: Exception) -> dict[str, Any]:
    return _event(at, ERROR,
                  f"The check stopped: {error}. The agent tries again at its next check.")


def limits_read(at: datetime, before: PolicyConfig | None, now: PolicyConfig,
                logged: tuple[Decimal, Decimal] | None = None) -> list[dict]:
    """The contract's limits, told only when they differ from the last ones seen: by this run,
    or, after a restart, the last ones written in the feed (`logged`)."""
    known = (before.max_per_payment, before.weekly_budget) if before is not None else logged
    if known == (now.max_per_payment, now.weekly_budget):
        return []
    return [_event(at, LIMITS, f"Limits from your contract: {fmt(now.max_per_payment)} USDC per "
                               f"payment, {fmt(now.weekly_budget)} USDC per week.",
                   per_payment=str(now.max_per_payment), weekly=str(now.weekly_budget))]


def logged_limits(folder: Path) -> tuple[Decimal, Decimal] | None:
    """The limits in the newest limits event of the feed, if it carries them."""
    for event in reversed(read_activity(folder, limit=MAX_EVENTS)):
        if event.get("kind") == LIMITS:
            try:
                return Decimal(event["per_payment"]), Decimal(event["weekly"])
            except (KeyError, ArithmeticError, TypeError, ValueError):
                return None
    return None


def inbox_events(at: datetime, signatures: Mapping[str, Mapping[str, str]],
                 answers: Mapping[str, Mapping[str, str]]) -> list[dict[str, Any]]:
    events = []
    for supplier, s in signatures.items():
        if s.get("result") == "ACCEPTED":
            events.append(_event(at, SIGNATURE, f"{supplier} confirmed its wallet "
                                                f"{_short(s.get('wallet', ''))} by signing."))
        else:
            events.append(_event(at, SIGNATURE, f"A signature from {supplier} was not accepted: "
                                                f"{s.get('reason', '')}."))
    for invoice, a in answers.items():
        verdict = "approval" if a.get("verdict") == "APPROVE" else "rejection"
        if a.get("result") == "ACCEPTED":
            events.append(_event(at, ANSWER, f"Your {verdict} of {invoice} is recorded.",
                                 invoice=invoice))
        else:
            events.append(_event(at, ANSWER, f"Your {verdict} of {invoice} was not used: "
                                             f"{a.get('reason', '')}.", invoice=invoice))
    return events


def agent_events(report: CycleReport, *, streamed: bool = False) -> list[dict[str, Any]]:
    """The agent's session in this cycle: why it woke, what it decided, its word to the owner."""
    return session_events(report.thought.session if report.thought else None, streamed=streamed)


def _started(at: datetime, wakes) -> dict[str, Any]:
    reasons = [w.text for w in wakes]
    woke = " ".join(reasons[:SHOWN_WAKES])
    if len(reasons) > SHOWN_WAKES:
        woke += f" (and {len(reasons) - SHOWN_WAKES} more)"
    return _event(at, REFLEX, f"The agent started working. {woke}")


def _tool(at: datetime, step: Mapping[str, Any]) -> dict[str, Any]:
    return _event(at, TOOL, tool_trace.line(step), tool=step.get("tool"),
                  refused=not step.get("ok", True), done_at=step.get("at"))


class LiveActivity:
    """Writes the agent's session into the feed as it happens: why it started, then each tool
    call the moment it returns. What it decided and said is written when it finishes."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def started(self, at: datetime, wakes) -> None:
        append_activity(self.folder, [_started(at, wakes)])

    def step(self, at: datetime, step: Mapping[str, Any]) -> None:
        append_activity(self.folder, [_tool(at, step)])


def session_events(session, *, streamed: bool = False) -> list[dict[str, Any]]:
    """One session of the agent as activity, from a check or from a chat. `streamed`: its start
    and its tool calls are already in the feed (LiveActivity), so they are not written again."""
    if session is None:
        return []
    at = session.at
    events = [] if streamed else [_started(at, session.wakes),
                                  *(_tool(at, s) for s in session.trace)]
    if not session.finished:
        events.append(_event(at, ERROR, f"The agent's session did not finish: {session.error}. "
                                        "Nothing new is paid; it tries again soon."))
        return events
    for d in session.decisions:  # the agent's own words; the page shows the choice apart
        events.append(_event(at, AGENT, f"{d.invoice}: {d.reason}", invoice=d.invoice,
                             choice=d.choice.value,
                             pay_on=d.pay_on.isoformat() if d.pay_on else None))
    events.extend(_event(at, AGENT, f"Will look again on {a.at:%Y-%m-%d %H:%M}: {a.why}")
                  for a in session.alarms)
    events.extend(_event(at, AGENT, _brought(m), tx=m.get("tx")) for m in session.funds_moved)
    events.extend(_event(at, AGENT, f"Emailed {supplier} from your ERPNext.")
                  for supplier in session.emails)
    events.extend(_event(at, AGENT, f"Sent {supplier} the notice to confirm its wallet.")
                  for supplier in session.wallet_notices)
    if session.summary:
        cost = shown(estimate(session.usage, session.model))
        events.append(_event(at, AGENT, session.summary, cost=cost,
                             tokens=session.usage.total, steps=session.steps))
    return events


def _brought(moved: Mapping[str, Any]) -> str:
    """A move of the owner's funds, in the owner's words: a deposit into their Unified Balance,
    or a spend from it to their wallet on Arc."""
    if moved.get("kind") == "deposit":
        return (f"Moved {moved['amount']} USDC from your wallet on "
                f"{moved['chain'].replace('_', ' ')} into your Unified Balance.")
    if moved.get("kind") == "earn_park":
        return (f"Parked {moved['amount']} USDC from your wallet on Arc into "
                f"{moved.get('vault_name') or moved.get('vault')}.")
    if moved.get("kind") == "earn_unpark":
        return (f"Withdrew {moved['amount']} USDC from "
                f"{moved.get('vault_name') or moved.get('vault')} to your wallet on Arc.")
    sources = ", ".join(f"{s['chain'].replace('_', ' ')} {s['amount']}" for s in moved["from"])
    return (f"Brought {moved['arrives_on_arc']} USDC to your wallet on Arc from your Unified "
            f"Balance ({sources}).")


def payment_events(at: datetime, payments) -> list[dict[str, Any]]:
    """What payments did: one line per invoice for how far its payment got (sent, paid with its
    transaction, booked in ERPNext), and a line for each one not paid."""
    events = []
    furthest = {e.invoice: e for e in payments if e.status in _SENT}
    for e in payments:
        if e.status in _SENT and furthest[e.invoice] is not e:
            continue
        amount, to = f"{fmt(e.amount)} USDC", _short(e.payee) or e.supplier
        if e.status is PaymentStatus.SUBMITTED:
            events.append(_event(at, SENDING, f"Sending {amount} to {e.supplier} ({to}) for "
                                              f"{e.invoice}.", invoice=e.invoice))
        elif e.status is PaymentStatus.COMPLETE:
            events.append(_event(at, PAID, f"Paid {amount} to {e.supplier} for {e.invoice}.",
                                 invoice=e.invoice, tx=e.tx_hash))
        elif e.status is PaymentStatus.RECORDED:
            events.append(_event(at, RECORDED, f"Paid {amount} to {e.supplier} for {e.invoice}, "
                                               f"recorded in ERPNext as {e.erp_entry}.",
                                 invoice=e.invoice, tx=e.tx_hash))
        else:
            events.append(_event(at, NOT_PAID, f"Did not pay {e.invoice}: {e.reason}.",
                                 invoice=e.invoice))
    return events


def cycle_events(report: CycleReport, interval_minutes: int) -> list[dict[str, Any]]:
    at = report.at
    events = []
    session = report.thought.session if report.thought else None
    decided = {d.invoice for d in session.decisions} if session and session.finished else set()
    for change in report.changes:
        d = change.decision
        if d.invoice in report.already_paid:
            continue  # paid or sent: its payment lines say what happened
        if change.kind is ChangeKind.CHANGED and d.invoice in decided:
            continue  # the agent's own line on it says more
        why = _why(d.action, d.reasons)
        if change.kind is ChangeKind.NEW:
            events.append(_event(at, NEW, f"New invoice {d.invoice} from {d.supplier}, "
                                          f"{fmt(d.amount)} USDC: {_ACTION[d.action]}{why}.",
                                 invoice=d.invoice, action=d.action.value))
        elif change.kind is ChangeKind.CHANGED:
            events.append(_event(at, CHANGED, _changed(d.invoice, d.supplier, d.action,
                                                       d.reasons),
                                 invoice=d.invoice, action=d.action.value))
    events.extend(_event(at, CLOSED, f"{invoice} is no longer unpaid in ERPNext.", invoice=invoice)
                  for invoice in report.closed)
    events.extend(_event(at, WAITING, f"Waiting for {c.supplier} to confirm its wallet "
                                      f"{_short(c.wallet)} on its page.")
                  for c in report.challenges if c.issued_at == at)
    s = report.settlement
    if s is not None:
        if s.problem:
            events.append(_event(at, ERROR, f"Payments could not be followed this check: "
                                            f"{s.problem}."))
        events.extend(payment_events(at, s.events))
        events.extend(_event(at, WAITING, f"{i} waits for room under your weekly limit.",
                             invoice=i) for i in s.waiting)
    plan = report.plan
    paid_now = sum((e.amount for e in (s.events if s else ())
                    if e.status is PaymentStatus.COMPLETE), start=Decimal(0))
    summary = (f"Check done: {len(report.decisions)} unpaid, {len(plan.asked)} need you, "
               f"{len(plan.held)} on hold")
    if paid_now:
        summary += f", {fmt(paid_now)} USDC paid"
    events.append(_event(at, DONE, f"{summary}. Next check in {interval_minutes} minutes.",
                         summary=summary))
    return events


def end_check(folder: Path, events: list[dict[str, Any]]) -> None:
    """Write what a check found. A check that found nothing (only its "done", with the same
    counts) joins the quiet checks right before it into one line, so days without news read as
    one line instead of one per check. That line says how many checks and since when, and
    names the events it replaces so a page that already shows them drops them."""
    old = read_activity(folder, limit=MAX_EVENTS)
    if not (len(events) == 1 and events[0]["kind"] == DONE and len(old) >= 2
            and old[-1]["kind"] == CHECK):
        append_activity(folder, events)
        return
    done, previous = events[0], old[-2]
    if previous["kind"] != DONE or previous.get("summary") != done.get("summary"):
        append_activity(folder, events)
        return
    if "checks" in previous:  # already a line of quiet checks
        replaced, since, checks = old[-2:], previous["since"], previous["checks"] + 1
    elif len(old) >= 3 and old[-3]["kind"] == CHECK:  # one quiet check: its start and its end
        replaced, since, checks = old[-3:], old[-3]["at"], 2
    else:  # the last check found something: it stays as it was
        append_activity(folder, events)
        return
    line = {**done, "text": f"Nothing changed in {checks} checks. {done['text']}",
            "since": since, "checks": checks, "replaces": [e["seq"] for e in replaced]}
    _save(folder, [*old[:-len(replaced)], {"seq": old[-1]["seq"] + 1, **line}])


def append_activity(folder: Path, events: list[dict[str, Any]]) -> None:
    """Number the events after the last one and keep only the newest MAX_EVENTS."""
    if not events:
        return
    old = read_activity(folder, limit=MAX_EVENTS)
    seq = old[-1]["seq"] if old else 0
    numbered = [{"seq": seq + i, **e} for i, e in enumerate(events, start=1)]
    _save(folder, old + numbered)


def _save(folder: Path, events: list[dict[str, Any]]) -> None:
    """Write the feed atomically, keeping only the newest MAX_EVENTS."""
    kept = events[-MAX_EVENTS:]
    path = folder / ACTIVITY
    tmp = path.with_name(ACTIVITY + ".tmp")
    tmp.write_text("".join(json.dumps(e) + "\n" for e in kept), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_activity(folder: Path, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    """Events with a sequence number above `after`, oldest first, at most `limit` (the newest)."""
    try:
        lines = (folder / ACTIVITY).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and int(event.get("seq", 0)) > after:
            events.append(event)
    return events[-limit:]


def history_events(entries) -> list[dict[str, Any]]:
    """The agent's memory told as activity, at the time each thing happened: what it decided
    on each invoice, each payment with its transaction, signatures, answers and limits."""
    events, seen = [], set()
    for kind, _run, invoice, at, body in entries:
        if kind == "DECISION":
            action = Action(body["action"])
            reasons = body.get("reasons") or []
            why = _why(action, reasons)
            if invoice in seen:
                events.append(_event(at, CHANGED, _changed(invoice, body["supplier"], action,
                                                           reasons),
                                     invoice=invoice, action=action.value))
            else:
                seen.add(invoice)
                events.append(_event(at, NEW, f"New invoice {invoice} from {body['supplier']}, "
                                              f"{fmt(Decimal(body['amount']))} USDC: "
                                              f"{_ACTION[action]}{why}.",
                                     invoice=invoice, action=action.value))
        elif kind == "PAYMENT":
            amount, status = f"{fmt(Decimal(body['amount']))} USDC", body["status"]
            supplier, tx = body.get("supplier", ""), body.get("tx_hash")
            if status == PaymentStatus.SUBMITTED.value:
                to = _short(body.get("payee", ""))
                events.append(_event(at, SENDING, f"Sending {amount} to {supplier} ({to}) for "
                                                  f"{invoice}.", invoice=invoice))
            elif status == PaymentStatus.COMPLETE.value and tx:
                events.append(_event(at, PAID, f"Paid {amount} to {supplier} for {invoice}.",
                                     invoice=invoice, tx=tx))
            elif status == PaymentStatus.RECORDED.value:
                events.append(_event(at, RECORDED, f"{invoice} recorded in ERPNext as "
                                                   f"{body.get('erp_entry')}.",
                                     invoice=invoice, tx=tx))
            elif status in (PaymentStatus.BLOCKED.value, PaymentStatus.FAILED.value):
                events.append(_event(at, NOT_PAID, f"Did not pay {invoice}: "
                                                   f"{body.get('reason', '')}.", invoice=invoice))
        elif kind == "ANSWER":
            verdict = "approval" if body.get("verdict") == "APPROVE" else "rejection"
            events.append(_event(at, ANSWER, f"Your {verdict} of {invoice} is recorded.",
                                 invoice=invoice))
        elif kind == "PROOF":
            events.append(_event(at, SIGNATURE, f"{body['supplier']} confirmed its wallet "
                                                f"{_short(body['wallet'])} by signing."))
        elif kind == "CHALLENGE":
            events.append(_event(at, WAITING, f"Waiting for {body['supplier']} to confirm its "
                                              f"wallet {_short(body['wallet'])} on its page."))
        elif kind == "POLICY":
            values = body.get("values") or {}
            per_payment = fmt(Decimal(values["max_per_payment"]))
            weekly = fmt(Decimal(values["weekly_budget"]))
            events.append(_event(at, LIMITS, f"Limits: {per_payment} USDC per payment, "
                                             f"{weekly} USDC per week."))
        elif kind == "CLOSED":
            events.append(_event(at, CLOSED, f"{invoice} is no longer unpaid in ERPNext.",
                                 invoice=invoice))
    return [{**e, "history": True} for e in events]


def backfill(folder: Path, entries) -> int:
    """Once per shop: put what the agent did before the feed existed in front of it, so the owner
    sees the whole story. Returns how many events were added."""
    current = read_activity(folder, limit=MAX_EVENTS)
    if any(e.get("history") for e in current):
        return 0
    first = current[0]["at"] if current else None
    past = [e for e in history_events(entries) if first is None or e["at"] < first]
    if not past:
        return 0
    merged = [{**e, "seq": i} for i, e in enumerate(
        [*past, *({k: v for k, v in e.items() if k != "seq"} for e in current)], start=1)]
    kept = merged[-MAX_EVENTS:]
    path = folder / ACTIVITY
    tmp = path.with_name(ACTIVITY + ".tmp")
    tmp.write_text("".join(json.dumps(e) + "\n" for e in kept), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return len(past)
