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

from embco.controls.base import fmt
from embco.decision import Action, PolicyConfig
from embco.journal import ChangeKind
from embco.payments import PaymentStatus
from embco.runner import CycleReport

ACTIVITY = "activity.jsonl"
MAX_EVENTS = 1000

CHECK, DONE, NEW, CHANGED, CLOSED = "check", "done", "new", "changed", "closed"
SENDING, PAID, RECORDED, NOT_PAID = "sending", "paid", "recorded", "not_paid"
SIGNATURE, ANSWER, LIMITS, ERROR, WAITING = "signature", "answer", "limits", "error", "waiting"

_ACTION = {Action.PAY: "will be paid", Action.HOLD: "on hold", Action.ASK: "needs you"}


def _short(wallet: str) -> str:
    return f"{wallet[:6]}...{wallet[-4:]}" if wallet and len(wallet) > 12 else wallet or ""


def _event(at: datetime, kind: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"at": at.isoformat(), "kind": kind, "text": text,
            **{k: v for k, v in extra.items() if v}}


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
