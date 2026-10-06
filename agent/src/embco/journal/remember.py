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
    closed: tuple[str, ...]


def remember(
    journal: DecisionJournal,
    decisions: Sequence[Decision],
    policy: PolicyConfig,
    *,
    at: datetime | None = None,
) -> RunMemory:
    """`decisions` must cover every unpaid invoice: any open invoice missing here is closed."""
    open_before = journal.open_invoices()
    changes = tuple(
        compare(d, journal.last_entry(d.invoice), d.invoice in open_before) for d in decisions
    )
    closed = tuple(sorted(open_before - {d.invoice for d in decisions}))
    run_id = journal.record_run(changes, closed, policy, at or datetime.now(UTC))
    return RunMemory(run_id, changes, closed)
