"""Where the owner's USDC was after each check, so the dashboard can draw the treasury over time.

Each check reads the owner's wallet on Arc, Earn and the other chains (`last_run.json`). This
keeps one point per reading in `<shop>/balances.jsonl`: a new line when a number changed, or
every hour while nothing did. Only the newest MAX_POINTS are kept. A check that could not read
every place adds no point, so a reading error never shows as money leaving.

`money_seen.json` keeps the reading after the agent's last session. When a place moves by a tenth
of the weekly budget or more from it, the agent is woken once to look; what it then does is its
call. Holding money never wakes it again: only a new change does.
"""

import json
import os
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from agent.guardrails.controls.base import fmt

BALANCES = "balances.jsonl"
SEEN = "money_seen.json"
WAKE_SHARE = Decimal("0.1")  # of the weekly budget: smaller moves do not wake the agent
MAX_POINTS = 3000
QUIET = timedelta(hours=1)
PLACES = ("wallet", "earn", "other_chains")


def balance_point(at: datetime, earn: dict[str, Any] | None,
                  unified: dict[str, Any] | None) -> dict[str, str] | None:
    """The owner's USDC by place from one check's views; None when a place could not be read."""
    if not earn or earn.get("error"):
        return None  # Earn's view carries the wallet on Arc: without it there is no reading
    other = Decimal(0)
    if unified is not None:
        wallets = unified.get("wallets")
        if unified.get("error") or not isinstance(wallets, list):
            return None
        try:
            gateway = sum((Decimal(c["available"]) + Decimal(c["arriving"])
                           for c in unified["chains"]), Decimal(0))
            other = gateway + sum((Decimal(w["in_wallet"]) for w in wallets), Decimal(0))
        except (KeyError, InvalidOperation, TypeError):
            return None
    try:
        wallet, in_earn = Decimal(earn["in_wallet"]), Decimal(earn["total_in_earn"])
    except (KeyError, InvalidOperation, TypeError):
        return None
    return {"at": at.isoformat(), "wallet": fmt(wallet), "earn": fmt(in_earn),
            "other_chains": fmt(other)}


def record_balance(folder: Path, point: dict[str, str] | None) -> None:
    """Add the point when a number changed or an hour passed since the last one."""
    if point is None:
        return
    old = read_balances(folder)
    if old:
        last = old[-1]
        same = all(Decimal(last[p]) == Decimal(point[p]) for p in PLACES)
        quiet = datetime.fromisoformat(point["at"]) - datetime.fromisoformat(last["at"]) < QUIET
        if same and quiet:
            return
    kept = (old + [point])[-MAX_POINTS:]
    path = folder / BALANCES
    tmp = path.with_name(BALANCES + ".tmp")
    tmp.write_text("".join(json.dumps(p) + "\n" for p in kept), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def money_changed(folder: Path, threshold: Decimal) -> str | None:
    """What moved since the agent last looked, when one place changed by `threshold` or more.

    The baseline is the reading after the agent's last session, so what it moved itself is
    never a reason. None when nothing moved that much, or before the first baseline."""
    seen, points = read_seen(folder), read_balances(folder)
    if seen is None or not points:
        return None
    now = points[-1]
    moves = [(place, Decimal(seen[place]), Decimal(now[place])) for place in PLACES]
    if all(abs(after - before) < threshold for _, before, after in moves):
        return None
    names = {"wallet": "your wallet on Arc", "earn": "Earn",
             "other_chains": "other chains (Gateway and the owner's wallets there)"}
    return "The owner's money changed since you last looked: " + "; ".join(
        f"{names[place]} {fmt(before)} -> {fmt(after)}"
        for place, before, after in moves if before != after) + "."


def remember_seen(folder: Path) -> None:
    """The latest reading becomes what the agent last looked at."""
    points = read_balances(folder)
    if points:
        path = folder / SEEN
        tmp = path.with_name(SEEN + ".tmp")
        tmp.write_text(json.dumps(points[-1]), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)


def read_seen(folder: Path) -> dict[str, str] | None:
    try:
        seen = json.loads((folder / SEEN).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return seen if isinstance(seen, dict) and all(p in seen for p in PLACES) else None


def read_balances(folder: Path) -> list[dict[str, str]]:
    """Every kept point, oldest first; lines that cannot be read are skipped."""
    try:
        lines = (folder / BALANCES).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    points = []
    for line in lines:
        try:
            point = json.loads(line)
        except ValueError:
            continue
        if isinstance(point, dict) and "at" in point and all(p in point for p in PLACES):
            points.append(point)
    return points
