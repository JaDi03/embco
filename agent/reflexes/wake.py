"""When to wake the agent, and why. The reflexes decide this without the model.

Every cycle (every 15 minutes) the reflexes look for a reason: an invoice that is new, changed or
still without the agent's decision, a payment that failed or was blocked, a due date close by
with no payment planned before it, an alarm the agent set, the daily round, or the heartbeat
when the agent has not looked for a while. No reason, no session: quiet cycles cost nothing.

A one-time reason (an alarm, a due date, the round) fires until a session that finished saw it.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta, tzinfo

from agent.guardrails.controls.base import fmt
from agent.guardrails.rules import Action, Decision, fingerprint
from agent.memory import Change, ChangeKind
from agent.models import AgentDecision, Alarm, Choice, Note, Session, Wake
from services.payments import PaymentEvent, PaymentStatus

HEARTBEAT = timedelta(hours=4)
RETRY_AFTER_FAILURE = timedelta(minutes=30)
DUE_SOON_DAYS = (3, 1, 0)  # 0: due today or overdue
RECENT_NOTES = 20


def wakes_for(
    *,
    now: datetime,
    zone: tzinfo,
    decisions: Sequence[Decision],
    changes: Sequence[Change],
    sessions: Sequence[Session],
    standing: dict[str, AgentDecision],
    done: set[str],
    payments: Sequence[PaymentEvent],
    round_hour: int,
    extra: Sequence[Wake] = (),
) -> list[Wake]:
    last = sessions[-1] if sessions else None
    fired = {w.key for s in sessions if s.finished for w in s.wakes if w.key}
    local = now.astimezone(zone)
    wakes: list[Wake] = []
    if last is None:
        wakes.append(Wake("start", "This is your first session: look at every open invoice."))
    wakes.extend(extra)
    told = set()
    for change in changes:
        d = change.decision
        if change.kind is ChangeKind.NEW:
            wakes.append(Wake("new", f"New invoice {d.invoice} from {d.supplier}, "
                                     f"{fmt(d.amount)} USDC.", d.invoice))
        elif change.kind is ChangeKind.CHANGED:
            wakes.append(Wake("changed", f"{d.invoice} changed: {change.note}", d.invoice))
        else:
            continue
        told.add(d.invoice)
    open_ = [d for d in decisions if d.invoice not in done]
    for d in open_:
        if d.invoice not in told and not _holds(standing.get(d.invoice), d):
            wakes.append(Wake("undecided", f"{d.invoice} has no decision from you yet.",
                              d.invoice))
    since = last.at if last else None
    for p in payments:
        if since and p.at > since and p.status in (PaymentStatus.FAILED, PaymentStatus.BLOCKED):
            wakes.append(Wake("payment", f"The payment of {p.invoice} was "
                                         f"{p.status.value.lower()}: {p.reason}", p.invoice))
    for d in open_:
        wakes.extend(_due_soon(d, standing.get(d.invoice), local.date(), fired))
    for alarm in due_alarms(sessions, now):
        key = _alarm_key(alarm)
        if key not in fired:
            wakes.append(Wake("alarm", f"Your alarm: {alarm.why}", key=key))
    round_key = f"round:{local.date().isoformat()}"
    if local.hour >= round_hour and round_key not in fired:
        wakes.append(Wake("round", "Daily round: look over everything.", key=round_key))
    if open_ and last and not wakes and now - last.at >= HEARTBEAT:
        wakes.append(Wake("heartbeat", f"Heartbeat: you have not looked for "
                                       f"{int((now - last.at).total_seconds() // 3600)} hours."))
    return wakes


def backing_off(sessions: Sequence[Session], now: datetime) -> bool:
    """After a session that did not finish, wait a little before trying again."""
    return bool(sessions) and not sessions[-1].finished and (
        now - sessions[-1].at < RETRY_AFTER_FAILURE)


def due_alarms(sessions: Sequence[Session], now: datetime) -> list[Alarm]:
    return [a for s in sessions if s.finished for a in s.alarms if a.at <= now]


def pending_alarms(sessions: Sequence[Session], now: datetime) -> list[Alarm]:
    return [a for s in sessions if s.finished for a in s.alarms if a.at > now]


def recent_notes(sessions: Sequence[Session]) -> list[Note]:
    return [n for s in sessions if s.finished for n in s.notes][-RECENT_NOTES:]


def _holds(standing: AgentDecision | None, decision: Decision) -> bool:
    return standing is not None and standing.fingerprint == fingerprint(decision)


def _due_soon(decision: Decision, standing: AgentDecision | None, today, fired) -> list[Wake]:
    if decision.action is not Action.PAY or decision.due_date is None:
        return []
    if _holds(standing, decision) and (standing.choice is Choice.PAY_NOW or (
            standing.choice is Choice.SCHEDULE and standing.pay_on <= decision.due_date)):
        return []
    days = (decision.due_date - today).days
    if days not in DUE_SOON_DAYS and days > 0:
        return []
    days = max(days, 0)
    key = f"due:{decision.invoice}:{days}"
    if key in fired:
        return []
    when = "is due today or overdue" if days == 0 else f"is due in {days} day(s)"
    return [Wake("due", f"{decision.invoice} {when} ({decision.due_date}) and no payment is "
                        "planned before it.", decision.invoice, key=key)]


def _alarm_key(alarm: Alarm) -> str:
    set_at = alarm.set_at.isoformat() if alarm.set_at else ""
    return f"alarm:{set_at}:{alarm.at.isoformat()}"
