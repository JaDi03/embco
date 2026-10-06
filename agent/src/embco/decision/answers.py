"""Owner answers to ASK decisions.

An answer counts only for the exact decision the owner was shown. If the amount or the reasons
change, the fingerprint changes and the owner is asked again. HOLD is never overridden here:
something is wrong with the invoice and it has to be fixed in the ERP.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from embco.decision.fingerprint import fingerprint
from embco.decision.models import Action, Decision


class Verdict(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


@dataclass(frozen=True)
class OwnerAnswer:
    invoice: str
    fingerprint: str
    verdict: Verdict
    answered_by: str
    answered_at: datetime
    note: str = ""


def apply_answer(decision: Decision, answer: OwnerAnswer | None) -> Decision:
    if answer is None or decision.action is not Action.ASK:
        return decision
    if answer.fingerprint != fingerprint(decision):
        return decision
    when = answer.answered_at.date().isoformat()
    if answer.verdict is Verdict.APPROVE:
        reason = f"approved by {answer.answered_by} on {when}"
        return replace(decision, action=Action.PAY, reasons=(reason, *decision.reasons))
    reason = f"rejected by {answer.answered_by} on {when}"
    if answer.note:
        reason = f"{reason}: {answer.note}"
    return replace(decision, action=Action.HOLD, reasons=(reason, *decision.reasons))


def apply_answers(
    decisions: Sequence[Decision], answers: Mapping[str, OwnerAnswer]
) -> list[Decision]:
    return [apply_answer(d, answers.get(d.invoice)) for d in decisions]
