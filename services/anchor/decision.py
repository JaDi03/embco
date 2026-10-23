"""Every journal entry behind one decision of the agent, each proved against the roots on Arc.

Which entries a decision rests on, and its MANIFEST, are in agent/memory/manifest.py. The proofs
are made when asked for, from the journal and the anchors, as a transparency log serves them
(RFC 6962): nothing is stored. Sessions written before sessions had an id cannot be gathered
this way; decisions written before the MANIFEST can be proved entry by entry, but nothing shows
that none of their entries was left out.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.memory.manifest import NoDecision
from agent.memory.manifest import decision_entries as _decision_entries
from services.anchor.audit import AuditError
from services.anchor.client import Anchor
from services.anchor.proof import ProofCheck, check_bundle, make_bundle


def decision_entries(entries: Sequence[Mapping[str, Any]], session: str,
                     invoice: str) -> list[int]:
    """The numbers (counted from 1) of the entries behind the agent's decision on `invoice` in
    `session`, oldest first."""
    try:
        return _decision_entries(entries, session, invoice)
    except NoDecision as error:
        raise AuditError(str(error)) from None


def make_decision_bundle(data: Mapping[str, Any], session: str, invoice: str,
                         anchors: Sequence[Anchor]) -> dict[str, Any]:
    """One proof per entry behind the decision. Fails when one of them is not anchored yet."""
    numbers = decision_entries(data["entries"], session, invoice)
    return {
        "shop": data["shop"],
        "session": session,
        "invoice": invoice,
        "proofs": [make_bundle(dict(data), n, anchors) for n in numbers],
    }


@dataclass(frozen=True)
class DecisionCheck:
    proofs: tuple[ProofCheck, ...]  # one per entry, in the bundle's order
    unlinked: str | None  # what does not belong to the decision, when something does not
    listed: bool  # the bundle holds the decision's MANIFEST, so a left-out entry would show
    missing: int  # entries the MANIFEST lists that the bundle does not hold

    @property
    def ok(self) -> bool:
        return (self.unlinked is None and self.listed and not self.missing
                and all(p.ok for p in self.proofs))


def check_decision_bundle(bundle: Mapping[str, Any], anchors: Sequence[Anchor]) -> DecisionCheck:
    """Check each proof against Arc, and that every proved entry belongs to this decision.
    Reads only the anchors from Arc; whatever the bundle claims is checked, not believed."""
    try:
        session, invoice, proofs = bundle["session"], bundle["invoice"], list(bundle["proofs"])
        # by `index`, the place the audit path proves, not by the `number` the bundle states
        found = [(p["index"], p["entry"], json.loads(p["entry"]["body"])) for p in proofs]
    except (KeyError, TypeError, ValueError):
        raise AuditError("not a decision proof: it needs the session, the invoice and "
                         "the proofs") from None
    if not proofs:
        raise AuditError("not a decision proof: it has no proofs")
    manifests = [(n, b) for n, e, b in found if e["kind"] == "MANIFEST"]
    held = {e["hash"] for _, e, _ in found}
    listed = manifests[0][1].get("entries", []) if len(manifests) == 1 else []
    return DecisionCheck(proofs=tuple(check_bundle(p, anchors) for p in proofs),
                         unlinked=_unlinked(found, session, invoice),
                         listed=len(manifests) == 1,
                         missing=sum(1 for h in listed if h not in held))


def decision_report(bundle: Mapping[str, Any], result: DecisionCheck) -> str:
    lines = [f"Decision of the agent on {bundle['invoice']}, session {bundle['session']}, "
             f"journal of {bundle['shop']}: {len(result.proofs)} entries", ""]
    for proof, check in zip(bundle["proofs"], result.proofs, strict=True):
        entry = proof["entry"]
        lines.append(f"  entry {proof['number']:>5}  {entry['kind']:<8}  {entry['at']}  "
                     + ("anchored, unchanged" if check.ok else "DOES NOT CHECK"))
    if result.unlinked:
        lines += ["", f"Not part of this decision: {result.unlinked}"]
    if result.missing:
        lines += ["", f"Left out: {result.missing} entries its MANIFEST lists are not here"]
    elif not result.listed:
        lines += ["", "No MANIFEST: this decision was written before decisions listed their "
                      "entries, so nothing shows that none was left out"]
    lines += ["", "RESULT: " + ("every entry of this decision was anchored on Arc, unchanged, "
                                "and none is missing." if result.ok
                                else "DOES NOT CHECK. Do not rely on this decision.")]
    return "\n".join(lines)


def _unlinked(found: Sequence[tuple[int, Mapping[str, Any], Mapping[str, Any]]], session: str,
              invoice: str) -> str | None:
    agents = [b for _, e, b in found if e["kind"] == "AGENT"]
    if len(agents) != 1:
        return "a decision proof must hold exactly one AGENT entry"
    manifests = [(n, b) for n, e, b in found if e["kind"] == "MANIFEST"]
    if len(manifests) > 1:
        return "a decision proof must hold at most one MANIFEST entry"
    mark = agents[0].get("fingerprint")
    runs = {e["run"]: b.get("policy") for _, e, b in found if e["kind"] == "RUN"}
    checked = {e["run"] for _, e, _ in found if e["kind"] == "DECISION"}
    for n, e, b in found:
        kind = e["kind"]
        linked = {
            "SESSION": b.get("id") == session,
            "AGENT": e["invoice"] == invoice and b.get("session") == session,
            "PAYMENT": e["invoice"] == invoice and b.get("session") == session,
            "MANIFEST": e["invoice"] == invoice and b.get("session") == session,
            "ANSWER": e["invoice"] == invoice and b.get("fingerprint") == mark,
            "DECISION": e["invoice"] == invoice and b.get("fingerprint") == mark
                        and e["run"] in runs,
            "RUN": e["run"] in checked,
            "POLICY": b.get("policy") in runs.values(),
        }.get(kind, False)
        if not linked:
            return f"the {kind} entry at {e['at']} is not part of this decision"
        if manifests and kind != "MANIFEST" and e["hash"] not in manifests[0][1].get("entries", []):
            # only a payment state written after the manifest can be missing from it
            if not (kind == "PAYMENT" and n > manifests[0][0]):
                return f"the {kind} entry at {e['at']} is not in the decision's MANIFEST"
    return None
