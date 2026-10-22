"""A witness for a shop's journal anchors: a small record kept by someone other than the agent.

Each run reads the anchors from Arc and keeps, for each one, the first time this witness saw it.
Against the owner's latest copy of the journal it checks every anchor that copy covers, and
reports the ones it cannot check yet. It raises an alert when an anchor it had seen changed or
disappeared, or when an anchor does not match the copy. Run it on a schedule (for example daily).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.memory.sqlite import verify_export
from services.anchor.audit import AuditError
from services.anchor.client import Anchor, journal_root


@dataclass(frozen=True)
class WitnessRun:
    alerts: tuple[str, ...]
    notes: tuple[str, ...]
    anchors_seen: int

    @property
    def ok(self) -> bool:
        return not self.alerts


def load_state(path: Path, shop: str) -> dict[str, Any]:
    if not path.exists():
        return {"shop": shop, "seen": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state["shop"] != shop or not isinstance(state["seen"], list):
            raise AuditError(f"{path} is a witness record for another shop")
    except (OSError, ValueError, KeyError, TypeError):
        raise AuditError(f"{path} is not a witness record") from None
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(path)


def witness(state: dict[str, Any], anchors: Sequence[Anchor], journal: dict[str, Any] | None,
            now: datetime) -> WitnessRun:
    """Compare the anchors on Arc with what this witness saw before and with the copy, and
    record the new anchors in `state` (changed in place)."""
    alerts: list[str] = []
    notes: list[str] = []
    seen = state["seen"]
    for i, old in enumerate(seen):
        if i >= len(anchors):
            alerts.append(f"Anchor {i + 1} (seen {old['first_seen']}) "
                          "is no longer on Arc.")
        elif (anchors[i].size, anchors[i].root) != (old["size"], old["root"]):
            alerts.append(f"Anchor {i + 1} changed since it was seen on {old['first_seen']}.")
    for i in range(len(seen), len(anchors)):
        a = anchors[i]
        seen.append({"size": a.size, "root": a.root, "first_seen": now.isoformat()})
        if journal is None:
            notes.append(f"Anchor {i + 1} ({a.size} entries): not checked, no copy given.")
            continue
        entries = journal["entries"]
        if a.size > len(entries):
            notes.append(f"Anchor {i + 1} ({a.size} entries): not checked yet, your copy has "
                         f"{len(entries)}. Download a newer journal and run this again.")
            continue
        if verify_export(entries[:a.size]) is not None:
            alerts.append(f"Anchor {i + 1}: the copy's first {a.size} entries "
                          "break the hash chain.")
        elif journal_root([e["hash"] for e in entries[:a.size]]).hex() != a.root:
            alerts.append(f"Anchor {i + 1} does NOT match your copy: entries were changed, "
                          "removed or reordered before it.")
        else:
            notes.append(f"Anchor {i + 1} ({a.size} entries): matches your copy.")
    return WitnessRun(alerts=tuple(alerts), notes=tuple(notes), anchors_seen=len(seen))
