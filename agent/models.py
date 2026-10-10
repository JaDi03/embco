"""What the agent decides in a session, and what it leaves for the next one.

The rules (controls) say whether an invoice may be paid. The agent decides what to do with it:
pay it now, pay it on a date, hold it, or ask the owner. Its decision counts only for the exact
rule decision it saw (the fingerprint); when the invoice changes, the agent decides again.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class Choice(StrEnum):
    PAY_NOW = "PAY_NOW"
    SCHEDULE = "SCHEDULE"  # pay on a date the agent chose
    HOLD = "HOLD"
    ASK = "ASK"


class Autonomy(StrEnum):
    OBSERVE = "observe"  # the agent decides and explains; nothing is paid
    ACT = "act"  # the agent pays within the contract's limits and asks about exceptions


@dataclass(frozen=True)
class AgentDecision:
    invoice: str
    fingerprint: str  # the rule decision this choice was made on
    choice: Choice
    reason: str
    pay_on: date | None = None  # SCHEDULE only
    question: str = ""  # ASK only
    recommendation: str = ""  # ASK only
    decided_at: datetime | None = None


@dataclass(frozen=True)
class Alarm:
    """A time the agent wants to look again, and why."""

    at: datetime
    why: str
    set_at: datetime | None = None


@dataclass(frozen=True)
class Note:
    about: str  # an invoice, a supplier, or "shop"
    text: str
    at: datetime | None = None


@dataclass(frozen=True)
class Wake:
    """Why the agent was woken: the reflexes say it in a few words."""

    kind: str
    text: str
    invoice: str | None = None
    key: str = ""  # identifies a one-time wake (an alarm, a due date) so it fires once


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return (self.input_tokens + self.output_tokens + self.cache_read_tokens
                + self.cache_write_tokens)

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens,
                     self.output_tokens + other.output_tokens,
                     self.cache_read_tokens + other.cache_read_tokens,
                     self.cache_write_tokens + other.cache_write_tokens)


@dataclass(frozen=True)
class Session:
    """One time the agent was woken: why, what it decided, and what it cost."""

    at: datetime
    wakes: tuple[Wake, ...]
    finished: bool
    summary: str = ""  # for the owner, 1-3 sentences
    decisions: tuple[AgentDecision, ...] = ()
    alarms: tuple[Alarm, ...] = ()
    notes: tuple[Note, ...] = ()
    steps: int = 0
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    error: str = ""  # why the session did not finish, when it did not
    replies: tuple[str, ...] = ()  # what the agent answered the owner in this session
    money: dict[str, str] | None = None  # what could be paid when the session ran, from the chain
    emails: tuple[str, ...] = ()  # suppliers the agent wrote to in this session
