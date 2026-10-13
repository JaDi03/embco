"""The contract every control follows: read a context, return one finding."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from agent.guardrails.controls.context import Context


class Outcome(StrEnum):
    PASS = "PASS"  # noqa: S105 (an outcome label, not a credential)
    HOLD = "HOLD"  # cannot be paid: no proven wallet, or a duplicate
    ASK = "ASK"  # the owner must answer first (a first payment to a new wallet)
    NOTE = "NOTE"  # worth weighing: the agent decides what to do about it


@dataclass(frozen=True)
class Finding:
    control: str
    outcome: Outcome
    reason: str


class Control(Protocol):
    name: str

    def check(self, ctx: Context) -> Finding: ...


def passed(control: str, reason: str) -> Finding:
    return Finding(control, Outcome.PASS, reason)


def hold(control: str, reason: str) -> Finding:
    return Finding(control, Outcome.HOLD, reason)


def ask(control: str, reason: str) -> Finding:
    return Finding(control, Outcome.ASK, reason)


def note(control: str, reason: str) -> Finding:
    return Finding(control, Outcome.NOTE, reason)


def fmt(value: Decimal) -> str:
    """Plain number without exponent or trailing zeros: 10.0 -> 10, 1E+1 never appears."""
    text = format(value.normalize(), "f")
    return text


def short(wallet: str) -> str:
    return f"{wallet[:6]}...{wallet[-4:]}"
