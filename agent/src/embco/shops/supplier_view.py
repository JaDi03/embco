"""What each supplier sees on its own page: its open invoices with this shop, its payments, and
the wallet signature the agent waits for. The shop's agent writes it after every cycle; the hub
serves one supplier's part to whoever holds that supplier's link.

A supplier never sees the shop's own checks: an invoice the agent holds or asks the owner about
is shown as under review, without the reason.
"""

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from embco.controls import WalletChallenge
from embco.journal import DecisionJournal
from embco.payments import PaymentEvent, PaymentStatus
from embco.runner import CycleReport
from embco.signing import ARC_TESTNET_CHAIN_ID, typed_data

PAID = "PAID"
PAYMENT_SENT = "PAYMENT_SENT"
SCHEDULED = "SCHEDULED"
SIGNATURE_NEEDED = "SIGNATURE_NEEDED"
UNDER_REVIEW = "UNDER_REVIEW"
NETWORK = "Arc testnet"  # payments are made in test USDC for now
_DONE = (PaymentStatus.COMPLETE, PaymentStatus.RECORDED)


def supplier_view(
    report: CycleReport,
    journal: DecisionJournal,
    company: str,
    signatures: dict[str, dict[str, str]] | None = None,
    chain_id: int = ARC_TESTNET_CHAIN_ID,
    wallet_of: Callable[[str], str | None] = lambda supplier: None,
) -> dict[str, Any]:
    """One entry per supplier with an open invoice, a payment or a challenge. `wallet` is the
    one on file in the ERP: only whoever signs in with it sees the entry."""
    payments = {p.invoice: p for p in journal.latest_payments()}
    challenges = {c.supplier: c for c in report.challenges}
    scheduled = {d.invoice for d in report.plan.pay_now}
    scheduled |= {d.decision.invoice for d in report.plan.deferred}
    invoices: dict[str, list] = defaultdict(list)
    for d in report.decisions:
        last = payments.get(d.invoice)
        if last and last.status in _DONE:
            continue  # listed with the payments
        if last and last.status is PaymentStatus.SUBMITTED:
            status = PAYMENT_SENT
        elif d.supplier in challenges:
            status = SIGNATURE_NEEDED
        elif d.invoice in scheduled:
            status = SCHEDULED
        else:
            status = UNDER_REVIEW
        invoices[d.supplier].append({
            "invoice": d.invoice, "amount": str(d.amount),
            "due_date": d.due_date.isoformat() if d.due_date else None, "status": status,
        })
    paid: dict[str, list] = defaultdict(list)
    for p in payments.values():
        if p.status in _DONE and p.tx_hash:
            paid[p.supplier].append(_payment(p))
    names = sorted(set(invoices) | set(paid) | set(challenges))
    return {
        "at": report.at.isoformat(),
        "company": company,
        "network": NETWORK,
        "suppliers": {
            name: {
                "wallet": wallet_of(name),
                "invoices": invoices.get(name, []),
                "payments": paid.get(name, []),
                "challenge": _challenge(challenges.get(name), chain_id),
                "last_signature": (signatures or {}).get(name),
            }
            for name in names
        },
    }


def _payment(p: PaymentEvent) -> dict[str, str]:
    return {"invoice": p.invoice, "amount": str(p.amount), "paid_at": p.at.isoformat(),
            "tx_hash": p.tx_hash or "", "wallet": p.payee}


def _challenge(challenge: WalletChallenge | None, chain_id: int) -> dict[str, Any] | None:
    if challenge is None:
        return None
    return {"wallet": challenge.wallet, "expires_at": challenge.expires_at.isoformat(),
            "typed_data": typed_data(challenge, chain_id)}
