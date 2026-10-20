"""Every time USDC entered or left Earn, kept for the dashboard's Earn card.

Read from the ShopEarn contract's own events (Parked, Unparked) on Arc. Each check reads only
the blocks since the last one and adds what it finds to `<shop>/earn_moves.json`: the Arc node
forgets old history, so what was read once is kept here.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from services.circle import CircleError
from services.earn import OwnerEarn
from services.payments import ChainError, PaymentSetupError

EARN_MOVES = "earn_moves.json"

log = logging.getLogger(__name__)


def update_moves(folder: Path, owner_earn: OwnerEarn | None) -> None:
    """Add the moves since the last read; a failed read is tried again next check."""
    if owner_earn is None:
        return
    saved = _read(folder)
    try:
        found, last = owner_earn.moves(saved["block"])
    except (ChainError, CircleError, PaymentSetupError, KeyError, ValueError) as error:
        log.warning("could not read Earn moves: %s", type(error).__name__)
        return
    known = {(m["tx"], m["kind"], m["usdc"]) for m in saved["moves"]}
    moves = saved["moves"] + [m for m in found if (m["tx"], m["kind"], m["usdc"]) not in known]
    path = folder / EARN_MOVES
    tmp = path.with_name(EARN_MOVES + ".tmp")
    tmp.write_text(json.dumps({"block": last, "moves": moves}, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_moves(folder: Path) -> list[dict[str, Any]]:
    return _read(folder)["moves"]


def _read(folder: Path) -> dict[str, Any]:
    try:
        saved = json.loads((folder / EARN_MOVES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"block": None, "moves": []}
    if not isinstance(saved, dict) or not isinstance(saved.get("moves"), list):
        return {"block": None, "moves": []}
    return {"block": saved.get("block"), "moves": saved["moves"]}
