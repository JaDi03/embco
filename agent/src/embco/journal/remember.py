"""One call per run: say what changed since last time, then write the run to the journal."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from embco.decision import Decision, PolicyConfig
from embco.journal.base import DecisionJournal
from embco.journal.changes import Change, compare


@dataclass(frozen=True)
class RunMemory:
    run_id: int
    changes: tuple[Change, ...]


def remember(
    journal: DecisionJournal,
    decisions: Sequence[Decision],
    policy: PolicyConfig,
    *,
    at: datetime | None = None,
) -> RunMemory:
    changes = tuple(compare(d, journal.last_entry(d.invoice)) for d in decisions)
    run_id = journal.record_run(decisions, policy, at or datetime.now(UTC))
    return RunMemory(run_id, changes)
