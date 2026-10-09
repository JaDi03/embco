"""The conversation between the owner and the shop's agent.

The hub drops each message from the owner in `<shop>/inbox/messages/`; the shop's process is the
only writer of `<shop>/chat.jsonl`: it moves the messages there at the start of a cycle and adds
the agent's replies after its session. A message is waiting for an answer until a reply from the
agent comes after it, so a session that fails leaves it waiting for the next one.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

INBOX_MESSAGES = Path("inbox") / "messages"
CHAT = "chat.jsonl"
MAX_ENTRIES = 500
MAX_TEXT = 1000
OWNER, AGENT, SYSTEM = "owner", "agent", "system"


def drop_message(folder: Path, text: str, by: str, at: datetime) -> None:
    box = folder / INBOX_MESSAGES
    box.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = f"{time.time_ns()}"
    tmp = box / f"{name}.tmp"
    tmp.write_text(json.dumps({"text": text[:MAX_TEXT], "by": by, "at": at.isoformat()}),
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, box / f"{name}.json")


def messages_waiting(folder: Path) -> bool:
    """Messages in the inbox the agent has not taken yet."""
    return any((folder / INBOX_MESSAGES).glob("*.json"))


def take_messages(folder: Path) -> list[dict[str, Any]]:
    """Move the owner's new messages into the conversation, oldest first."""
    taken = []
    for path in sorted((folder / INBOX_MESSAGES).glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            taken.append({"from": OWNER, "at": str(item["at"]), "by": str(item["by"]),
                          "text": str(item["text"])[:MAX_TEXT]})
        except (OSError, ValueError, KeyError, TypeError):
            pass  # unreadable: dropped, never half-read
        path.unlink(missing_ok=True)
    append_chat(folder, taken)
    return taken


def append_chat(folder: Path, entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    old = read_chat(folder, limit=MAX_ENTRIES)
    seq = old[-1]["seq"] if old else 0
    kept = (old + [{"seq": seq + i, **e} for i, e in enumerate(entries, start=1)])[-MAX_ENTRIES:]
    path = folder / CHAT
    tmp = path.with_name(CHAT + ".tmp")
    tmp.write_text("".join(json.dumps(e) + "\n" for e in kept), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_chat(folder: Path, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    try:
        lines = (folder / CHAT).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and int(entry.get("seq", 0)) > after:
            entries.append(entry)
    return entries[-limit:]


def unanswered(folder: Path) -> list[dict[str, Any]]:
    """The owner's messages after the agent's last reply."""
    pending: list[dict[str, Any]] = []
    for entry in read_chat(folder, limit=MAX_ENTRIES):
        if entry.get("from") == AGENT:
            pending = []
        elif entry.get("from") == OWNER:
            pending.append(entry)
    return pending
