"""Which journal entries one decision of the agent rests on, and the MANIFEST that lists them.

A decision is the AGENT entry for one invoice in one session. What it rests on: the SESSION that
made it (the tools the model called and what they returned), the rule check it was made on (the
DECISION entry with the same fingerprint), the RUN of that check and the POLICY it names, the
owner's answers to that check, and the PAYMENT entries the session sent for the invoice.

When the session is written, the journal adds one MANIFEST entry per decision with the hashes of
those entries. A proof of the decision that leaves one of them out no longer checks: the
manifest is anchored with the rest, so it cannot be changed to match. Payment states written
after the session (a payment followed up in a later cycle) come after the manifest and are not
in it.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any


class NoDecision(LookupError):
    """The journal has no choice of the agent on that invoice in that session."""


def decision_entries(entries: Sequence[Mapping[str, Any]], session: str,
                     invoice: str) -> list[int]:
    """The numbers (counted from 1) of the entries behind the agent's decision on `invoice` in
    `session`, oldest first, its MANIFEST included when it has one."""
    rows = [(n, e, json.loads(e["body"])) for n, e in enumerate(entries, start=1)]
    agent = next((n for n, e, b in rows if e["kind"] == "AGENT" and e["invoice"] == invoice
                  and b.get("session") == session), None)
    if agent is None:
        raise NoDecision(f"no decision on {invoice} in session {session}")
    mark = rows[agent - 1][2]["fingerprint"]
    found = {agent}
    found |= {n for n, e, b in rows if e["kind"] == "SESSION" and b.get("id") == session}
    found |= {n for n, e, b in rows if e["kind"] in ("PAYMENT", "MANIFEST")
              and e["invoice"] == invoice and b.get("session") == session}
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


def manifest_body(entries: Sequence[Mapping[str, Any]], session: str,
                  invoice: str) -> dict[str, Any]:
    """The MANIFEST of a decision just written: the hashes of every entry it rests on so far, and
    what it says in short, taken from those entries: the model's choice and reason, the invoice
    amount and supplier from the rule check, and the payment's state and transaction. Free text is
    UTF-8 hex, so a reader on chain (DecisionVerifier) never has to undo JSON escapes."""
    numbers = decision_entries(entries, session, invoice)
    picked = [(entries[n - 1]["kind"], json.loads(entries[n - 1]["body"])) for n in numbers]
    agent = next(b for k, b in picked if k == "AGENT")
    check = next((b for k, b in picked if k == "DECISION"), {})
    payments = [b for k, b in picked if k == "PAYMENT"]
    return {
        "session": session,
        "entries": [entries[n - 1]["hash"] for n in numbers],
        "choice": agent.get("choice", ""),
        "reason_hex": agent.get("reason", "").encode().hex(),
        "amount": check.get("amount") or (payments[-1]["amount"] if payments else ""),
        "supplier_hex": check.get("supplier", "").encode().hex(),
        "payment": payments[-1]["status"] if payments else "",
        "tx": next((b["tx_hash"] for b in reversed(payments) if b.get("tx_hash")), ""),
    }
