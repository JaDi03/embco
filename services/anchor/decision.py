"""Every journal entry behind one decision of the agent, each proved against the roots on Arc.

A decision is the AGENT entry for one invoice in one session. What it rests on: the SESSION that
made it (the tools the model called and what they returned), the rule check it was made on (the
DECISION entry with the same fingerprint), the RUN of that check and the POLICY it names, the
owner's answers to that check, and the PAYMENT entries the session sent for the invoice.

The proofs are made when asked for, from the journal and the anchors, as a transparency log
serves them (RFC 6962): nothing is stored. Sessions written before sessions had an id cannot be
gathered this way.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.anchor.audit import AuditError
from services.anchor.client import Anchor
from services.anchor.proof import ProofCheck, check_bundle, make_bundle


def decision_entries(entries: Sequence[Mapping[str, Any]], session: str,
                     invoice: str) -> list[int]:
    """The numbers (counted from 1) of the entries behind the agent's decision on `invoice` in
    `session`, oldest first."""
    rows = [(n, e, json.loads(e["body"])) for n, e in enumerate(entries, start=1)]
    agent = next((n for n, e, b in rows if e["kind"] == "AGENT" and e["invoice"] == invoice
                  and b.get("session") == session), None)
    if agent is None:
        raise AuditError(f"no decision on {invoice} in session {session}")
    mark = rows[agent - 1][2]["fingerprint"]
    found = {agent}
    found |= {n for n, e, b in rows if e["kind"] == "SESSION" and b.get("id") == session}
    found |= {n for n, e, b in rows if e["kind"] == "PAYMENT" and e["invoice"] == invoice
              and b.get("session") == session}
    found |= {n for n, e, b in rows if n < agent and e["kind"] == "ANSWER"
              and e["invoice"] == invoice and b.get("fingerprint") == mark}
    check = max((n for n, e, b in rows if n < agent and e["kind"] == "DECISION"
                 and e["invoice"] == invoice and b.get("fingerprint") == mark), default=None)
    if check is not None:
        found.add(check)
        run = rows[check - 1][1]["run"]
        run_entry = next((n for n, e, _ in rows if e["kind"] == "RUN" and e["run"] == run), None)
        if run_entry is not None:
            found.add(run_entry)
            policy = rows[run_entry - 1][2].get("policy")
            policy_entry = max((n for n, e, b in rows if n < run_entry and e["kind"] == "POLICY"
                                and b.get("policy") == policy), default=None)
            if policy_entry is not None:
                found.add(policy_entry)
    return sorted(found)


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

    @property
    def ok(self) -> bool:
        return self.unlinked is None and all(p.ok for p in self.proofs)


def check_decision_bundle(bundle: Mapping[str, Any], anchors: Sequence[Anchor]) -> DecisionCheck:
    """Check each proof against Arc, and that every proved entry belongs to this decision.
    Reads only the anchors from Arc; whatever the bundle claims is checked, not believed."""
    try:
        session, invoice, proofs = bundle["session"], bundle["invoice"], list(bundle["proofs"])
        found = [(p["entry"], json.loads(p["entry"]["body"])) for p in proofs]
    except (KeyError, TypeError, ValueError):
        raise AuditError("not a decision proof: it needs the session, the invoice and "
                         "the proofs") from None
    if not proofs:
        raise AuditError("not a decision proof: it has no proofs")
    return DecisionCheck(proofs=tuple(check_bundle(p, anchors) for p in proofs),
                         unlinked=_unlinked(found, session, invoice))


def decision_report(bundle: Mapping[str, Any], result: DecisionCheck) -> str:
    lines = [f"Decision of the agent on {bundle['invoice']}, session {bundle['session']}, "
             f"journal of {bundle['shop']}: {len(result.proofs)} entries", ""]
    for proof, check in zip(bundle["proofs"], result.proofs, strict=True):
        entry = proof["entry"]
        lines.append(f"  entry {proof['number']:>5}  {entry['kind']:<8}  {entry['at']}  "
                     + ("anchored, unchanged" if check.ok else "DOES NOT CHECK"))
    if result.unlinked:
        lines += ["", f"Not part of this decision: {result.unlinked}"]
    lines += ["", "RESULT: " + ("every entry of this decision was anchored on Arc, unchanged."
                                if result.ok else "DOES NOT CHECK. Do not rely on this decision.")]
    return "\n".join(lines)


def _unlinked(found: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]], session: str,
              invoice: str) -> str | None:
    agents = [b for e, b in found if e["kind"] == "AGENT"]
    if len(agents) != 1:
        return "a decision proof must hold exactly one AGENT entry"
    mark = agents[0].get("fingerprint")
    runs = {e["run"]: b.get("policy") for e, b in found if e["kind"] == "RUN"}
    checked = {e["run"] for e, _ in found if e["kind"] == "DECISION"}
    for e, b in found:
        kind = e["kind"]
        linked = {
            "SESSION": b.get("id") == session,
            "AGENT": e["invoice"] == invoice and b.get("session") == session,
            "PAYMENT": e["invoice"] == invoice and b.get("session") == session,
            "ANSWER": e["invoice"] == invoice and b.get("fingerprint") == mark,
            "DECISION": e["invoice"] == invoice and b.get("fingerprint") == mark
                        and e["run"] in runs,
            "RUN": e["run"] in checked,
            "POLICY": b.get("policy") in runs.values(),
        }.get(kind, False)
        if not linked:
            return f"the {kind} entry at {e['at']} is not part of this decision"
    return None
