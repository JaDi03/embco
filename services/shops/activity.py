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

from agent.guardrails.controls.base import fmt
from agent.guardrails.rules import Action, PolicyConfig
from agent.memory import ChangeKind
from agent.models import Choice
from agent.reflexes.cycle import CycleReport
from services.payments import PaymentStatus

ACTIVITY = "activity.jsonl"
MAX_EVENTS = 1000

CHECK, DONE, NEW, CHANGED, CLOSED = "check", "done", "new", "changed", "closed"
SENDING, PAID, RECORDED, NOT_PAID = "sending", "paid", "recorded", "not_paid"
SIGNATURE, ANSWER, LIMITS, ERROR, WAITING = "signature", "answer", "limits", "error", "waiting"
AGENT, REFLEX = "agent", "reflex"  # what the agent decided; what woke it
YOU = "you"  # what the owner did from the dashboard
SHOWN_WAKES = 3

_ACTION = {Action.PAY: "the checks pass", Action.HOLD: "on hold", Action.ASK: "needs you"}


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


def check_started(at: datetime) -> dict[str, Any]:
    return _event(at, CHECK, "Checking your ERPNext for unpaid invoices...")


def check_failed(at: datetime, error: Exception) -> dict[str, Any]:
    return _event(at, ERROR,
                  f"The check stopped: {error}. The agent tries again at its next check.")


def limits_read(at: datetime, before: PolicyConfig | None, now: PolicyConfig) -> list[dict]:
    if before is not None and (before.max_per_payment, before.weekly_budget) == (
            now.max_per_payment, now.weekly_budget):
        return []
    return [_event(at, LIMITS, f"Limits from your contract: {fmt(now.max_per_payment)} USDC per "
                               f"payment, {fmt(now.weekly_budget)} USDC per week.")]


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


def agent_events(report: CycleReport) -> list[dict[str, Any]]:
    """The agent's session in this cycle: why it woke, what it decided, its word to the owner."""
    session = report.thought.session if report.thought else None
    if session is None:
        return []
    at = session.at
    reasons = [w.text for w in session.wakes]
    woke = " ".join(reasons[:SHOWN_WAKES])
    if len(reasons) > SHOWN_WAKES:
        woke += f" (and {len(reasons) - SHOWN_WAKES} more)"
    events = [_event(at, REFLEX, f"Woke the agent. {woke}")]
    if not session.finished:
        events.append(_event(at, ERROR, f"The agent's session did not finish: {session.error}. "
                                        "Nothing new is paid; it tries again soon."))
        return events
    for d in session.decisions:
        events.append(_event(at, AGENT, f"{d.invoice}: {_CHOICE[d.choice](d)} {d.reason}",
                             invoice=d.invoice, choice=d.choice.value))
    events.extend(_event(at, AGENT, f"Will look again on {a.at:%Y-%m-%d %H:%M}: {a.why}")
                  for a in session.alarms)
    if session.summary:
        events.append(_event(at, AGENT, session.summary))
    return events


_CHOICE = {
    Choice.PAY_NOW: lambda d: "paying now.",
    Choice.SCHEDULE: lambda d: f"will pay on {d.pay_on}.",
    Choice.HOLD: lambda d: "holding it.",
    Choice.ASK: lambda d: "asking you.",
}


def cycle_events(report: CycleReport, interval_minutes: int) -> list[dict[str, Any]]:
    at = report.at
    events = []
    for change in report.changes:
        d = change.decision
        why = f": {d.reasons[0]}" if d.reasons and d.action is not Action.PAY else ""
        if change.kind is ChangeKind.NEW:
            events.append(_event(at, NEW, f"New invoice {d.invoice} from {d.supplier}, "
                                          f"{fmt(d.amount)} USDC: {_ACTION[d.action]}{why}.",
                                 invoice=d.invoice, action=d.action.value))
        elif change.kind is ChangeKind.CHANGED:
            events.append(_event(at, CHANGED, f"{d.invoice} from {d.supplier} is now "
                                              f"{_ACTION[d.action]}{why}.",
                                 invoice=d.invoice, action=d.action.value))
    events.extend(_event(at, CLOSED, f"{invoice} is no longer unpaid in ERPNext.", invoice=invoice)
                  for invoice in report.closed)
    events.extend(_event(at, WAITING, f"Waiting for {c.supplier} to confirm its wallet "
                                      f"{_short(c.wallet)} on its page.")
                  for c in report.challenges if c.issued_at == at)
    s = report.settlement
    if s is not None:
        if s.problem:
            events.append(_event(at, ERROR, f"No payments this check: {s.problem}."))
        for e in s.events:
            amount, to = f"{fmt(e.amount)} USDC", _short(e.payee) or e.supplier
            if e.status is PaymentStatus.SUBMITTED:
                events.append(_event(at, SENDING, f"Sending {amount} to {e.supplier} ({to}) for "
                                                  f"{e.invoice}.", invoice=e.invoice))
            elif e.status is PaymentStatus.COMPLETE:
                events.append(_event(at, PAID, f"Paid {amount} to {e.supplier} for {e.invoice}.",
                                     invoice=e.invoice, tx=e.tx_hash))
            elif e.status is PaymentStatus.RECORDED:
                events.append(_event(at, RECORDED, f"{e.invoice} recorded in ERPNext as "
                                                   f"{e.erp_entry}.", invoice=e.invoice,
                                     tx=e.tx_hash))
            else:
                events.append(_event(at, NOT_PAID, f"Did not pay {e.invoice}: {e.reason}.",
                                     invoice=e.invoice))
        events.extend(_event(at, WAITING, f"{i} waits for room under your weekly limit.",
                             invoice=i) for i in s.waiting)
    plan = report.plan
    paid_now = sum((e.amount for e in (s.events if s else ())
                    if e.status is PaymentStatus.COMPLETE), start=Decimal(0))
    summary = (f"Check done: {len(report.decisions)} unpaid, {len(plan.asked)} need you, "
               f"{len(plan.held)} on hold")
    if paid_now:
        summary += f", {fmt(paid_now)} USDC paid"
    events.append(_event(at, DONE, f"{summary}. Next check in {interval_minutes} minutes."))
    return events


def append_activity(folder: Path, events: list[dict[str, Any]]) -> None:
    """Number the events after the last one and keep only the newest MAX_EVENTS."""
    if not events:
        return
    path = folder / ACTIVITY
    old = read_activity(folder, limit=MAX_EVENTS)
    seq = old[-1]["seq"] if old else 0
    numbered = [{"seq": seq + i, **e} for i, e in enumerate(events, start=1)]
    kept = (old + numbered)[-MAX_EVENTS:]
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
            why = f": {reasons[0]}" if reasons and action is not Action.PAY else ""
            if invoice in seen:
                events.append(_event(at, CHANGED, f"{invoice} from {body['supplier']} is now "
                                                  f"{_ACTION[action]}{why}.",
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
