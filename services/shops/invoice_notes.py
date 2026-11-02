"""The agent's decision on each open invoice, written in that invoice's timeline in the ERP, so the
owner reads it where they work, without opening the dashboard.

Only a change is written: a new choice (pay now, pay on a date, hold, ask the owner) or new facts
behind it (the rule decision's fingerprint). A check that decides the same again writes nothing.
The words are the model's own reason; the code only adds which choice it was and when it was
made, to find it in the journal. Writing the note never stops a payment: when the ERP does not
answer, the note is tried again at the next check.
"""

import html
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from agent.guardrails.rules import Decision, fingerprint
from agent.models import AgentDecision, Choice
from services.erp import LedgerError

log = logging.getLogger("services.shops")

NOTES_FILE = "invoice_notes.json"  # the last decision written on each invoice

CHOICES = {Choice.PAY_NOW: "Pay now", Choice.SCHEDULE: "Pay on {pay_on}", Choice.HOLD: "Hold",
           Choice.ASK: "Ask the owner"}

Comment = Callable[[str, str], None]  # (invoice, escaped html)


def current_decisions(decisions: Iterable[Decision],
                      agent: Mapping[str, AgentDecision]) -> list[AgentDecision]:
    """The agent's decision that still holds on each open invoice: made on today's facts."""
    return [agent[d.invoice] for d in decisions
            if d.invoice in agent and agent[d.invoice].fingerprint == fingerprint(d)]


def note_key(decision: AgentDecision) -> str:
    return f"{decision.choice.value}:{decision.pay_on or ''}:{decision.fingerprint}"


def note_html(decision: AgentDecision) -> str:
    choice = CHOICES[decision.choice].format(pay_on=decision.pay_on)
    lines = [f"<b>embco agent: {html.escape(choice)}</b>", html.escape(decision.reason)]
    if decision.choice is Choice.ASK and decision.recommendation:
        lines.append(html.escape(f"Recommendation: {decision.recommendation}"))
    if decision.decided_at:
        lines.append(html.escape(f"Decided {decision.decided_at:%Y-%m-%d %H:%M} UTC, kept in "
                                 "the embco journal."))
    return "<br>".join(lines)


def write_notes(folder: Path, decisions: Iterable[AgentDecision], comment: Comment | None) -> int:
    """Comment on every invoice whose decision is not written yet; returns how many."""
    if comment is None:
        return 0
    path = folder / NOTES_FILE
    try:
        written = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        written = {}
    count = 0
    for decision in decisions:
        key = note_key(decision)
        if written.get(decision.invoice) == key:
            continue
        try:
            comment(decision.invoice, note_html(decision))
        except LedgerError as error:
            log.warning("decision on %s not written in the ERP yet: %s", decision.invoice, error)
            continue
        written[decision.invoice] = key
        count += 1
    if count:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(written, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    return count
