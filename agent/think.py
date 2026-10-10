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
from agent.guardrails.rules import Decision, PolicyConfig
from agent.memory import Change, DecisionJournal
from agent.models import AgentDecision, Autonomy, Session, Wake
from agent.prompt import briefing, chat_briefing
from agent.reflexes.wake import backing_off, pending_alarms, recent_notes, wakes_for
from agent.tools import Toolbox
from services.erp import LedgerAdapter

log = logging.getLogger("agent")

DAILY_TOKENS = 2_000_000


@dataclass(frozen=True)
class BrainSetup:
    """How a shop's agent thinks: the model, the shop's clock and how far it may go alone."""

    brain: Brain
    zone: tzinfo
    round_hour: int = 8
    autonomy: Autonomy = Autonomy.ACT
    daily_tokens: int = DAILY_TOKENS
    room: Callable[[], Decimal | None] = field(default=lambda: None)
    chat_brain: Brain | None = None  # answers the owner; faster, lower effort


@dataclass(frozen=True)
class Thought:
    agent: dict[str, AgentDecision]  # the agent's decisions that stand after this cycle
    session: Session | None = None  # the session of this cycle, if the agent was woken
    skipped: str = ""  # why there was a reason to wake but no session
    last: Session | None = None  # the most recent session, this one or an earlier one


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
) -> Thought:
    """`messages` are what the owner wrote since the agent's last reply; `conversation` the
    recent exchange, for context."""
    sessions = journal.sessions()
    standing = journal.latest_agent_decisions()
    wakes = wakes_for(now=now, zone=setup.zone, decisions=decisions, changes=changes,
                      sessions=sessions, standing=standing, done=done,
                      payments=journal.latest_payments(), round_hour=setup.round_hour,
                      extra=extra)
    last = sessions[-1] if sessions else None
    if not wakes:
        return Thought(agent=standing, last=last)
    if backing_off(sessions, now):
        return Thought(agent=standing, last=last,
                       skipped="the last session failed; trying again soon")
    today = now.astimezone(setup.zone).date()
    used = sum(s.usage.total for s in sessions if s.at.astimezone(setup.zone).date() == today)
    if used >= setup.daily_tokens:
        log.warning("agent not woken: %d tokens used today (cap %d)", used, setup.daily_tokens)
        return Thought(agent=standing, last=last,
                       skipped="the agent's daily budget is used up")
    toolbox = Toolbox(decisions=list(decisions), ledger=ledger, journal=journal, policy=policy,
                      now=now, zone=setup.zone, done=set(done), standing=standing,
                      room=setup.room, past_notes=tuple(recent_notes(sessions)),
                      messages=tuple(messages))
    text = briefing(toolbox, wakes, pending_alarms(sessions, now), recent_notes(sessions),
                    conversation)
    try:
        run = setup.brain.run(toolbox, text)
        finished, error, steps, usage = run.finished, run.error, run.steps, run.usage
    except Exception:  # a surprise in the loop must not stop the reflexes
        log.exception("agent session crashed")
        finished, error, steps, usage = False, "the session crashed", 0, None
    session = Session(
        at=now, wakes=tuple(wakes), finished=finished, summary=toolbox.summary or "",
        decisions=tuple(toolbox.chosen.values()) if finished else (),
        alarms=tuple(toolbox.alarms) if finished else (),
        notes=tuple(toolbox.notes) if finished else (),
        steps=steps, model=setup.brain.model, error=error,
        replies=tuple(toolbox.replies) if finished else (),
        **({"usage": usage} if usage else {}),
    )
    journal.record_session(session)
    if not finished:
        log.warning("agent session did not finish: %s", error)
    return Thought(agent={**standing, **{d.invoice: d for d in session.decisions}},
                   session=session, last=session)


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
) -> Thought:
    """Answer the owner between two checks, on the decisions of the last check: no new look at
    the whole ERP. The agent reads only what the question needs, with its tools. Whatever it
    chooses on an invoice is applied, and paid if so, by the next full check."""
    sessions = journal.sessions()
    standing = journal.latest_agent_decisions()
    last = sessions[-1] if sessions else None
    if backing_off(sessions, now):
        return Thought(agent=standing, last=last,
                       skipped="the last session failed; trying again soon")
    today = now.astimezone(setup.zone).date()
    used = sum(s.usage.total for s in sessions if s.at.astimezone(setup.zone).date() == today)
    if used >= setup.daily_tokens:
        return Thought(agent=standing, last=last,
                       skipped="the agent's daily budget is used up")
    brain = setup.chat_brain or setup.brain
    toolbox = Toolbox(decisions=list(decisions), ledger=ledger, journal=journal, policy=policy,
                      now=now, zone=setup.zone, done=set(done), standing=standing,
                      room=setup.room, past_notes=tuple(recent_notes(sessions)),
                      messages=tuple(messages), require_all=False)
    text = chat_briefing(toolbox, pending_alarms(sessions, now), recent_notes(sessions),
                         conversation)
    try:
        run = brain.run(toolbox, text)
        finished, error, steps, usage = run.finished, run.error, run.steps, run.usage
    except Exception:  # a surprise in the loop must not stop the reflexes
        log.exception("agent chat session crashed")
        finished, error, steps, usage = False, "the session crashed", 0, None
    session = Session(
        at=now, wakes=(Wake("message", f"The owner wrote to you ({len(messages)} message(s))."),),
        finished=finished, summary=toolbox.summary or "",
        decisions=tuple(toolbox.chosen.values()) if finished else (),
        alarms=tuple(toolbox.alarms) if finished else (),
        notes=tuple(toolbox.notes) if finished else (),
        steps=steps, model=brain.model, error=error,
        replies=tuple(toolbox.replies) if finished else (),
        **({"usage": usage} if usage else {}),
    )
    journal.record_session(session)
    return Thought(agent={**standing, **{d.invoice: d for d in session.decisions}},
                   session=session, last=session)
