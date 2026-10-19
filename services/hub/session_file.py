"""Owner sign-ins kept on disk, so a restart of the hub (every deploy) signs nobody out.

Only a SHA-256 hash of each session token is written, never the token: reading the file does
not let anyone sign in. The file is written atomically and readable by the hub's user only.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load(path: Path) -> dict[str, dict[str, Any]]:
    """{token hash: {"shop", "owner", "expires_at"}}; empty without a file or when unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(path: Path, entries: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(entries), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
