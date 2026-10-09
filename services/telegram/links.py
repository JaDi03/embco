"""Which Telegram chat speaks for which shop.

The owner, signed in on the dashboard, asks for a one-time code and sends `/link <code>` to the
bot. Only a hash of the code is kept, it expires in 10 minutes and works once. From then on the
bot listens to that chat only, for that shop.
"""

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

CODE_FILE = "telegram_code.json"
LINK_FILE = "telegram.json"
CODE_TTL = timedelta(minutes=10)
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I to misread


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().replace("-", "").encode()).hexdigest()


def _write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def new_code(folder: Path, now: datetime) -> tuple[str, datetime]:
    """A fresh code replaces any earlier one."""
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    expires = now + CODE_TTL
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / CODE_FILE, {"hash": _hash(raw), "expires_at": expires.isoformat()})
    return f"{raw[:4]}-{raw[4:]}", expires


def claim(folder: Path, code: str, chat_id: int, now: datetime) -> bool:
    """Link the chat to this shop if the code is its own, unexpired and unused."""
    pending = _read(folder / CODE_FILE)
    if not pending or not secrets.compare_digest(pending.get("hash", ""), _hash(code)):
        return False
    (folder / CODE_FILE).unlink(missing_ok=True)  # used once, valid or not
    if datetime.fromisoformat(pending["expires_at"]) < now:
        return False
    _write(folder / LINK_FILE, {"chat_id": chat_id, "linked_at": now.isoformat(),
                                "forwarded": 0})
    return True


def link_of(folder: Path) -> dict[str, Any] | None:
    return _read(folder / LINK_FILE)


def unlink(folder: Path) -> None:
    (folder / LINK_FILE).unlink(missing_ok=True)
    (folder / CODE_FILE).unlink(missing_ok=True)


def set_forwarded(folder: Path, seq: int) -> None:
    """The last conversation entry already sent to the chat."""
    link = link_of(folder)
    if link is not None:
        _write(folder / LINK_FILE, {**link, "forwarded": seq})
