"""One entry of the journal, proved against the roots on Arc.

A proof bundle carries the entry itself, the hash of the entry before it (so the entry's content
can be recomputed and checked against its hash), and the Merkle audit path (RFC 9162) from that
hash to the root of the first `size` entries. The checker recomputes everything, and takes the
root only from Arc: a bundle that claims a root Arc never wrote fails.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from agent.memory.merkle import inclusion_proof, verify_inclusion
from agent.memory.models import GENESIS
from agent.memory.sqlite import entry_hash_of
from services.anchor.audit import AuditError
from services.anchor.client import Anchor, journal_root

ENTRY_FIELDS = ("kind", "run", "invoice", "at", "body", "hash")


@dataclass(frozen=True)
class ProofCheck:
    content_matches_hash: bool  # the entry's text hashes to its stored hash, after the one before
    included: bool  # that hash is in the tree whose root is `root`
    anchored: bool  # that root, with that size, is an anchor written on Arc
    anchored_at: str | None

    @property
    def ok(self) -> bool:
        return self.content_matches_hash and self.included and self.anchored


def make_bundle(data: dict[str, Any], number: int, anchors: Sequence[Anchor]) -> dict[str, Any]:
    """The proof that entry `number` (counted from 1, as the audit page lists them) is in the
    journal anchored on Arc. Uses the first anchor that covers it."""
    entries = data["entries"]
    if not 1 <= number <= len(entries):
        raise AuditError(f"this file has entries 1 to {len(entries)}, not {number}")
    covering = [a for a in anchors if a.size >= number]
    if not covering:
        raise AuditError(f"entry {number} is not covered by an anchor on Arc yet")
    anchor = min(covering, key=lambda a: a.size)
    hashes = [e["hash"] for e in entries[:anchor.size]]
    if journal_root(hashes).hex() != anchor.root:
        raise AuditError(f"the first {anchor.size} entries of this file do not match "
                         "Arc, so no proof can be made from it")
    index = number - 1
    leaves = [bytes.fromhex(h) for h in hashes]
    return {
        "shop": data["shop"],
        "number": number,
        "index": index,
        "size": anchor.size,
        "root": anchor.root,
        "previous_hash": hashes[index - 1] if index else GENESIS,
        "entry": {f: entries[index][f] for f in ENTRY_FIELDS},
        "proof": [p.hex() for p in inclusion_proof(leaves, index)],
        "how_to_check": "embco check-proof <this file> --rpc-url <Arc Testnet node>",
    }


def check_bundle(bundle: dict[str, Any], anchors: Sequence[Anchor]) -> ProofCheck:
    """Recompute the entry, its place in the tree and whether Arc holds that root. Reads only
    the anchors from Arc; whatever the bundle claims is checked, not believed."""
    try:
        entry = bundle["entry"]
        previous = bundle["previous_hash"]
        index, size = int(bundle["index"]), int(bundle["size"])
        root = bytes.fromhex(bundle["root"])
        proof = [bytes.fromhex(p) for p in bundle["proof"]]
        stored = entry["hash"]
    except (KeyError, TypeError, ValueError):
        raise AuditError("not a proof bundle: it needs the entry, index, size, root "
                         "and proof") from None
    content_ok = entry_hash_of(previous, entry) == stored
    included = verify_inclusion(bytes.fromhex(stored), index, size, proof, root)
    anchor = next((a for a in anchors if a.size == size and bytes.fromhex(a.root) == root), None)
    return ProofCheck(
        content_matches_hash=content_ok,
        included=included,
        anchored=anchor is not None,
        anchored_at=anchor.anchored_at.isoformat() if anchor and anchor.anchored_at else None,
    )


SHOWN_TEXT = 400  # the full text is in the proof file; a report needs only its start


def proof_report(bundle: dict[str, Any], result: ProofCheck) -> str:
    entry = bundle["entry"]
    head = " ".join(part for part in (entry["kind"], entry.get("invoice"), "at", entry["at"])
                    if part)
    body = entry["body"]
    if len(body) > SHOWN_TEXT:
        body = body[:SHOWN_TEXT] + f"... [{len(body)} characters in the proof file]"
    lines = [
        f"Entry {bundle['number']} of the journal of {bundle['shop']}: {head}",
        f"Its text: {body}",
        "",
        "Its hash follows from the entry before it:  "
        + ("yes" if result.content_matches_hash else "NO"),
        f"It is in the tree of the first {bundle['size']} entries:  "
        + ("yes" if result.included else "NO"),
        "That tree's root is written on Arc:  "
        + (f"yes, {result.anchored_at}" if result.anchored else "NO"),
        "",
        "RESULT: " + ("this entry was anchored on Arc, unchanged." if result.ok
                      else "DOES NOT CHECK. Do not rely on this entry."),
    ]
    return "\n".join(lines)


def read_bundle(path: str) -> dict[str, Any]:
    import json
    from pathlib import Path

    try:
        bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AuditError("not a proof file: it must be the JSON written by embco prove") from None
    if not isinstance(bundle, dict) or "entry" not in bundle or "proof" not in bundle:
        raise AuditError("not a proof file: it must be the JSON written by embco prove")
    return bundle
