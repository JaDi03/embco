"""Wake the agent when the reflexes find a reason, run one session, and remember it.

A session that does not finish is remembered too (why it failed, what it cost) but changes no
decision: the payments planned from it are the ones the agent had already decided.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from decimal import Decimal
from typing import Any

from agent.agent import Brain
from agent.cost import estimate, shown
from agent.guardrails.rules import Decision, PolicyConfig
from agent.memory import Change, DecisionJournal, JournalEntry
from agent.models import AgentDecision, Autonomy, Choice, Session, Wake, payment_seen_key
from agent.prompt import already_told, briefing, chat_briefing
from agent.reflexes.funds import funds_wake, money_wake
from agent.reflexes.wake import backing_off, pending_alarms, recent_notes, wakes_for
from agent.tools import Toolbox
from services.erp import LedgerAdapter
from services.payments import PaymentEvent

log = logging.getLogger("agent")

DAILY_BUDGET = Decimal("1.00")  # USD a day (shop time) for the model, estimated from the tokens
DAILY_TOKENS = 2_000_000  # the ceiling for sessions of a model with no known price
BUDGET_USED = "the agent's daily budget is used up"


def decided_on(journal: DecisionJournal,
               standing: Mapping[str, AgentDecision]) -> dict[str, JournalEntry]:
    """The rule decision each of the agent's holds was made on, to tell a hold that only lost
    reasons since from one with something new."""
    found = {}
    for invoice, choice in standing.items():
        if choice.choice is Choice.HOLD:
            match = [e for e in journal.history(invoice) if e.fingerprint == choice.fingerprint]
            if match:
                found[invoice] = match[-1]
    return found


def over_budget(setup: "BrainSetup", sessions: Sequence[Session], now: datetime) -> str:
    """Why the agent may not be woken again today, or "" when it may: what today's sessions
    cost reached the daily budget. Sessions of a model with no known price count by tokens
    instead, against the ceiling."""
    today = now.astimezone(setup.zone).date()
    todays = [s for s in sessions if s.at.astimezone(setup.zone).date() == today]
    costs = [(s, estimate(s.usage, s.model)) for s in todays]
    spent = sum((c for _, c in costs if c is not None), Decimal(0))
    tokens = sum(s.usage.total for s, c in costs if c is None)
    if spent >= setup.daily_budget or tokens >= setup.daily_tokens:
        log.warning("agent not woken: %s and %d tokens used today (budget %s, ceiling %d)",
                    shown(spent), tokens, shown(setup.daily_budget), setup.daily_tokens)
        return (f"{BUDGET_USED} ({shown(spent)} of {shown(setup.daily_budget)}); it is woken "
                "again tomorrow, shop time")
    return ""


@dataclass(frozen=True)
class BrainSetup:
    """How a shop's agent thinks: the model, the shop's clock and how far it may go alone."""

    brain: Brain
    zone: tzinfo
    round_hour: int = 8
    autonomy: Autonomy = Autonomy.ACT
    daily_budget: Decimal = DAILY_BUDGET
    daily_tokens: int = DAILY_TOKENS
    room: Callable[[], Decimal | None] = field(default=lambda: None)
    chat_brain: Brain | None = None  # answers the owner; faster, lower effort
    funds: Callable[[], dict[str, str] | None] = field(default=lambda: None)
    mailer: Callable[[str, str, str], str] | None = None  # writes to a supplier from the ERP
    wallet_notice: Callable[[str], str] | None = None  # the standard wallet notice, from the ERP
    owner_funds: Any = None  # the owner's Unified Balance (services.unified_balance.OwnerFunds)
    owner_earn: Any = None  # the owner's Arc Earn vaults (services.earn.OwnerEarn)


@dataclass(frozen=True)
class Thought:
    agent: dict[str, AgentDecision]  # the agent's decisions that stand after this cycle
    session: Session | None = None  # the session of this cycle, if the agent was woken
    skipped: str = ""  # why there was a reason to wake but no session
    last: Session | None = None  # the most recent session, this one or an earlier one
    payments: tuple[PaymentEvent, ...] = ()  # what the agent's payments did in the session


def think(
    setup: BrainSetup,
    *,
    journal: DecisionJournal,
    ledger: LedgerAdapter,
    decisions: Sequence[Decision],
    changes: Sequence[Change],
    policy: PolicyConfig,
    done: set[str],
    now: datetime,
    extra: Sequence[Wake] = (),
    messages: Sequence[str] = (),
    conversation: Sequence[Mapping[str, Any]] = (),
    payer: Any = None,
) -> Thought:
    """`messages` are what the owner wrote since the agent's last reply; `conversation` the
    recent exchange, for context. With a `payer` the agent pays during its session and sees
    what happened; without one its payment choices are only recorded."""
    sessions = journal.sessions()
    standing = journal.latest_agent_decisions()
    fired = {w.key for s in sessions if s.finished for w in s.wakes if w.key}
    money = setup.funds()
    short = funds_wake(decisions, standing, done, money, now.astimezone(setup.zone).date(),
                       fired)
    moved = money_wake(sessions, money, decisions, done)
    wakes = wakes_for(now=now, zone=setup.zone, decisions=decisions, changes=changes,
                      sessions=sessions, standing=standing, done=done,
                      payments=journal.latest_payments(), round_hour=setup.round_hour,
                      extra=[*extra, *([short] if short else []), *([moved] if moved else [])],
                      can_pay=payer is not None and setup.autonomy is Autonomy.ACT,
                      decided_on=decided_on(journal, standing))
    last = sessions[-1] if sessions else None
    if not wakes:
        return Thought(agent=standing, last=last)
    if backing_off(sessions, now):
        return Thought(agent=standing, last=last,
                       skipped="the last session failed; trying again soon")
    if spent := over_budget(setup, sessions, now):
        return Thought(agent=standing, last=last, skipped=spent)
    toolbox = Toolbox(decisions=list(decisions), ledger=ledger, journal=journal, policy=policy,
                      now=now, zone=setup.zone, done=set(done), standing=standing,
                      room=setup.room, funds=setup.funds, mailer=setup.mailer,
                      wallet_notice=setup.wallet_notice, owner_funds=setup.owner_funds,
                      owner_earn=setup.owner_earn,
                      emails_today=emails_today(sessions, setup.zone, now),
                      past_notes=tuple(recent_notes(sessions)),
                      messages=tuple(messages), payer=payer,
                      can_pay=setup.autonomy is Autonomy.ACT)
    text = briefing(toolbox, wakes, pending_alarms(sessions, now, done), recent_notes(sessions),
                    conversation, already_told(sessions, setup.zone, now))
    try:
        run = setup.brain.run(toolbox, text)
        finished, error, steps, usage = run.finished, run.error, run.steps, run.usage
    except Exception:  # a surprise in the loop must not stop the reflexes
        log.exception("agent session crashed")
        finished, error, steps, usage = False, "the session crashed", 0, None
    session = Session(
        at=now, wakes=tuple(wakes), finished=finished, summary=toolbox.summary or "",
        money=money_after(setup, toolbox, money),
        decisions=tuple(toolbox.chosen.values()) if finished else (),
        alarms=tuple(toolbox.alarms) if finished else (),
        notes=tuple(toolbox.notes) if finished else (),
        steps=steps, model=setup.brain.model, error=error,
        replies=tuple(toolbox.replies) if finished else (),
        emails=tuple(toolbox.emailed),
        wallet_notices=tuple(toolbox.notified_wallets),
        seen_payments=tuple(payment_seen_key(e) for e in toolbox.payments),
        funds_moved=tuple(toolbox.funds_moved),
        **({"usage": usage} if usage else {}),
    )
    journal.record_session(session)
    if not finished:
        log.warning("agent session did not finish: %s", error)
    return Thought(agent={**standing, **{d.invoice: d for d in session.decisions}},
                   session=session, last=session, payments=tuple(toolbox.payments))


def converse(
    setup: BrainSetup,
    *,
    journal: DecisionJournal,
    ledger: LedgerAdapter,
    decisions: Sequence[Decision],
    policy: PolicyConfig,
    done: set[str],
    now: datetime,
    messages: Sequence[str],
    conversation: Sequence[Mapping[str, Any]] = (),
    payer: Any = None,
) -> Thought:
    """Answer the owner between two checks, on the decisions of the last check: no new look at
    the whole ERP. The agent reads only what the question needs, with its tools, and acts on
    what the owner asks: a payment it makes here goes out now."""
    sessions = journal.sessions()
    standing = journal.latest_agent_decisions()
    last = sessions[-1] if sessions else None
    if backing_off(sessions, now):
        return Thought(agent=standing, last=last,
                       skipped="the last session failed; trying again soon")
    if spent := over_budget(setup, sessions, now):
        return Thought(agent=standing, last=last, skipped=spent)
    brain = setup.chat_brain or setup.brain
    toolbox = Toolbox(decisions=list(decisions), ledger=ledger, journal=journal, policy=policy,
                      now=now, zone=setup.zone, done=set(done), standing=standing,
                      room=setup.room, funds=setup.funds, mailer=setup.mailer,
                      wallet_notice=setup.wallet_notice, owner_funds=setup.owner_funds,
                      owner_earn=setup.owner_earn,
                      emails_today=emails_today(sessions, setup.zone, now),
                      past_notes=tuple(recent_notes(sessions)),
                      messages=tuple(messages), require_all=False, payer=payer,
                      can_pay=setup.autonomy is Autonomy.ACT)
    text = chat_briefing(toolbox, pending_alarms(sessions, now, done), recent_notes(sessions),
                         conversation, already_told(sessions, setup.zone, now))
    try:
        run = brain.run(toolbox, text)
        finished, error, steps, usage = run.finished, run.error, run.steps, run.usage
    except Exception:  # a surprise in the loop must not stop the reflexes
        log.exception("agent chat session crashed")
        finished, error, steps, usage = False, "the session crashed", 0, None
    session = Session(
        at=now, wakes=(Wake("message", f"The owner wrote to you ({len(messages)} message(s))."),),
        finished=finished, summary=toolbox.summary or "",
        money=money_after(setup, toolbox, None),
        decisions=tuple(toolbox.chosen.values()) if finished else (),
        alarms=tuple(toolbox.alarms) if finished else (),
        notes=tuple(toolbox.notes) if finished else (),
        steps=steps, model=brain.model, error=error,
        replies=tuple(toolbox.replies) if finished else (),
        emails=tuple(toolbox.emailed),
        wallet_notices=tuple(toolbox.notified_wallets),
        seen_payments=tuple(payment_seen_key(e) for e in toolbox.payments),
        funds_moved=tuple(toolbox.funds_moved),
        **({"usage": usage} if usage else {}),
    )
    journal.record_session(session)
    return Thought(agent={**standing, **{d.invoice: d for d in session.decisions}},
                   session=session, last=session, payments=tuple(toolbox.payments))


def money_after(setup: BrainSetup, toolbox: Toolbox,
                before: dict[str, str] | None) -> dict[str, str] | None:
    """The money the agent leaves the session knowing: read again when it paid or brought funds,
    so what it did itself is not a change of money that wakes it in the next check."""
    if not (toolbox.payments or toolbox.funds_moved):
        return before
    return setup.funds() or before


def emails_today(sessions: Sequence[Session], zone: tzinfo, now: datetime) -> int:
    """Emails to suppliers already sent today (shop time), for the daily cap."""
    today = now.astimezone(zone).date()
    return sum(len(s.emails) for s in sessions if s.at.astimezone(zone).date() == today)
