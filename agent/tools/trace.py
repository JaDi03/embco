"""The agent's tool calls, in order, as the record of how it worked a session out.

Each step is what the model asked for (the tool and the values it chose) and what came back:
done, with the transaction when money moved, or refused with the reason the model was given.
The model's long texts (reasons, messages, summaries) are left out here: they are kept with the
decision, the message or the summary they belong to.
"""

import json
from collections.abc import Mapping
from typing import Any

MAX_VALUE = 60
MAX_REFUSAL = 160
WORDS = frozenset({"reason", "text", "body", "subject", "summary", "question", "recommendation"})


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tx(result: str) -> str:
    """The transaction in a tool's JSON result, when it has one."""
    try:
        body = json.loads(result)
    except (TypeError, ValueError):
        return ""
    return str(body.get("tx") or "") if isinstance(body, dict) else ""


def step(name: str, args: Mapping[str, Any], *, result: str | None = None,
         refused: str | None = None) -> dict[str, Any]:
    """One call: {"tool", "args", "ok", "tx"?, "refused"?}."""
    shown = {k: _clip(v, MAX_VALUE) for k, v in sorted(args.items()) if k not in WORDS}
    out: dict[str, Any] = {"tool": name, "args": shown, "ok": refused is None}
    if refused is not None:
        out["refused"] = _clip(refused, MAX_REFUSAL)
    elif tx := _tx(result or ""):
        out["tx"] = tx
    return out


def line(s: Mapping[str, Any]) -> str:
    """'pay_now(invoice=ACC-PINV-2026-00017) -> done, tx 0xabc...' for the owner's activity."""
    args = ", ".join(f"{k}={v}" for k, v in s.get("args", {}).items())
    call = f"{s['tool']}({args})"
    if not s.get("ok", True):
        return f"{call} -> refused: {s.get('refused', '')}"
    return f"{call} -> done, tx {s['tx']}" if s.get("tx") else f"{call} -> done"
