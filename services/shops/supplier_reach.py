"""Whether the ERP has an email for each supplier the agent is waiting on, so adding one wakes it.

A supplier whose invoice the checks do not let pay yet (no proven wallet, or the owner must answer
first) often needs a word from the shop, and the wallet notice goes to the email on its record.
Adding that email in ERPNext changes no invoice, so nothing else would wake the agent before its
next round.

`reach_seen.json` keeps what the agent saw after its last session, like the money. A supplier
that gained or lost its email since wakes the agent once; what it does then is its call. A
supplier seen for the first time is only remembered: there is nothing to compare it with.
"""

import json
import os
from collections.abc import Callable, Iterable
from pathlib import Path

from agent.guardrails.rules import Action, Decision
from agent.models import Wake
from services.erp import LedgerError

SEEN = "reach_seen.json"


def reach_now(decisions: Iterable[Decision] | None, done: set[str],
              has_email: Callable[[str], bool]) -> dict[str, bool]:
    """For each supplier with an open invoice the checks do not let pay, whether the ERP has an
    email for it. A supplier the ERP did not answer for is left out."""
    waiting = sorted({d.supplier for d in decisions or ()
                      if d.invoice not in done and d.action is not Action.PAY})
    reach = {}
    for supplier in waiting:
        try:
            reach[supplier] = bool(has_email(supplier))
        except LedgerError:
            continue
    return reach


def reach_wakes(folder: Path, now: dict[str, bool]) -> list[Wake]:
    """A wake for each supplier whose email changed since the agent last looked."""
    seen = read_reach(folder)
    wakes = [Wake("supplier", f"{supplier} " + ("now has an email in the ERP." if has
                                                else "no longer has an email in the ERP."))
             for supplier, has in sorted(now.items()) if supplier in seen and seen[supplier] != has]
    if new := {s: has for s, has in now.items() if s not in seen}:
        _write(folder, {**seen, **new})
    return wakes


def remember_reach(folder: Path, now: dict[str, bool]) -> None:
    """What the agent saw in its finished session becomes what it last looked at."""
    if now:
        _write(folder, {**read_reach(folder), **now})


def read_reach(folder: Path) -> dict[str, bool]:
    try:
        seen = json.loads((folder / SEEN).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(seen, dict):
        return {}
    return {s: has for s, has in seen.items() if isinstance(s, str) and isinstance(has, bool)}


def _write(folder: Path, seen: dict[str, bool]) -> None:
    path = folder / SEEN
    tmp = path.with_name(SEEN + ".tmp")
    tmp.write_text(json.dumps(seen, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
