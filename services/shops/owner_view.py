"""What the owner's dashboard shows for each decision: the steps still missing, in a form the
page can turn into buttons, and where the payment is.

Each finding that is not a pass becomes a step with a fixed name, so the page never has to read
the agent's sentences to know what to offer. The sentence stays, for the owner to read.
"""

from typing import Any

from agent.guardrails.controls.base import Finding, Outcome
from agent.guardrails.rules import Decision, fingerprint
from agent.models import AgentDecision
from services.payments import PaymentEvent

WALLET_MISSING = "wallet_missing"  # no wallet in the ERP: add one there
WALLET_PROBLEM = "wallet_problem"  # the wallet in the ERP cannot be used: fix it there
SUPPLIER_SIGNATURE = "supplier_signature"  # waiting for the supplier to confirm its wallet
FIRST_PAYMENT = "first_payment"  # wallet proven; the owner says whether the supplier asked
OVER_LIMIT = "over_limit"  # above the per-payment limit in the contract
ORDER_RECEIPT = "order_receipt"  # the invoice does not match an order and a receipt
OTHER = "other"


def step_of(finding: Finding) -> str:
    if finding.control == "payee_wallet":
        if finding.outcome is Outcome.ASK:
            return FIRST_PAYMENT
        if "waiting for the supplier to sign" in finding.reason:
            return SUPPLIER_SIGNATURE
        if "no wallet on file" in finding.reason:
            return WALLET_MISSING
        return WALLET_PROBLEM
    if finding.control == "payment_limit":
        return OVER_LIMIT
    if finding.control == "three_way_match":
        return ORDER_RECEIPT
    return OTHER


def decision_view(decision: Decision, payment: PaymentEvent | None,
                  agent: AgentDecision | None = None) -> dict[str, Any]:
    """`agent` is the agent's choice on this invoice, shown only while it still applies."""
    if agent is not None and agent.fingerprint != fingerprint(decision):
        agent = None
    return {
        "invoice": decision.invoice,
        "supplier": decision.supplier,
        "amount": str(decision.amount),
        "due_date": decision.due_date.isoformat() if decision.due_date else None,
        "action": decision.action.value,
        "reasons": list(decision.reasons),
        "fingerprint": fingerprint(decision),
        "findings": [
            {"step": step_of(f), "control": f.control, "outcome": f.outcome.value,
             "reason": f.reason}
            for f in decision.findings if f.outcome is not Outcome.PASS
        ],
        "payment": None if payment is None else {
            "status": payment.status.value, "tx_hash": payment.tx_hash,
            "erp_entry": payment.erp_entry, "reason": payment.reason,
        },
        "agent": None if agent is None else {
            "choice": agent.choice.value, "reason": agent.reason,
            "pay_on": agent.pay_on.isoformat() if agent.pay_on else None,
            "question": agent.question or None, "recommendation": agent.recommendation or None,
        },
    }
