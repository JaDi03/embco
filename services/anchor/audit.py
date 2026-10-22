"""Audit a downloaded journal: recompute its hash chain and its Merkle roots from the file alone,
and compare them with what the agent wrote on Arc. Nothing in the file is taken on trust except
the entries themselves, and any change to those shows up as a different root."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.memory.sqlite import verify_export
from services.anchor.client import Anchor, journal_root


class AuditError(Exception):
    pass


@dataclass(frozen=True)
class AnchorRow:
    number: int
    size: int
    anchored_at: str
    arc_root: str
    file_root: str | None  # None when the file has fewer entries than the anchor covers
    matches: bool


@dataclass(frozen=True)
class Audit:
    shop: str
    entries: int
    chain_problem: str | None
    root: str  # the root of the whole file
    rows: tuple[AnchorRow, ...]

    @property
    def ok(self) -> bool:
        return self.chain_problem is None and all(r.matches for r in self.rows)


def load(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        entries, shop = data["entries"], data["shop"]
        for e in entries:
            for field in ("kind", "run", "invoice", "at", "body", "hash"):
                e[field]
    except (OSError, ValueError, KeyError, TypeError):
        raise AuditError("not an embco journal download: it needs a shop and its entries "
                         "(kind, run, invoice, at, body, hash)") from None
    if not isinstance(entries, list) or not isinstance(shop, str):
        raise AuditError("not an embco journal download")
    return data


def audit(data: dict[str, Any], anchors: Sequence[Anchor]) -> Audit:
    """The file against every anchor. The anchors come from Arc, never from the file."""
    entries = data["entries"]
    hashes = [e["hash"] for e in entries]
    rows = []
    for number, a in enumerate(anchors, start=1):
        root = journal_root(hashes[:a.size]).hex() if a.size <= len(hashes) else None
        rows.append(AnchorRow(number, a.size, a.anchored_at.strftime("%Y-%m-%d %H:%M UTC")
                              if a.anchored_at else "", a.root, root, root == a.root))
    return Audit(shop=data["shop"], entries=len(entries), chain_problem=verify_export(entries),
                 root=journal_root(hashes).hex(), rows=tuple(rows))


def report(result: Audit) -> str:
    lines = [f"Journal of {result.shop}: {result.entries} entries",
             "Hash chain: " + ("intact" if result.chain_problem is None
                               else f"BROKEN, {result.chain_problem}"),
             f"Merkle root of the whole file: {result.root}", ""]
    if result.rows:
        lines.append(f"{'#':>3}  {'entries':>7}  {'written on Arc':<21}  {'root on Arc':<14}  "
                     f"{'root from the file':<18}")
        for r in result.rows:
            mark = ("OK" if r.matches
                    else "MISSING ENTRIES" if r.file_root is None else "DIFFERENT")
            lines.append(f"{r.number:>3}  {r.size:>7}  {r.anchored_at:<21}  {r.arc_root[:12]}..  "
                         f"{(r.file_root or '-')[:12]}..      {mark}")
    else:
        lines.append("Nothing is anchored on Arc for this shop yet.")
    lines.append("")
    if result.ok and result.rows:
        lines.append(f"RESULT: the file matches every anchor on Arc; the last covers "
                     f"{result.rows[-1].size} of its {result.entries} entries.")
    elif result.ok:
        lines.append("RESULT: the chain is intact, but there is nothing on Arc to compare with.")
    else:
        lines.append("RESULT: DOES NOT MATCH. Something was changed, removed or reordered.")
    return "\n".join(lines)
