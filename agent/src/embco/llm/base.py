"""The explainer contract. Its output is shown to the owner and never changes a decision."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field

from embco.decision import Decision


class ExplanationText(BaseModel):
    """What the model must return, validated against this schema."""

    summary: str = Field(description="One or two plain sentences on what happened and why.")
    next_step: str = Field(description="One concrete thing the owner can do now.")


@dataclass(frozen=True)
class Explanation:
    invoice: str
    fingerprint: str
    summary: str
    next_step: str
    model: str
    created_at: datetime


class ExplainerError(Exception):
    """The explanation could not be produced. The cycle goes on without it."""


class Explainer(Protocol):
    model: str

    def explain(self, decision: Decision, change_note: str) -> ExplanationText: ...
