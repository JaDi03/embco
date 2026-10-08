"""Payee change control: the wallet we are about to pay must be the one paid last time.

A wallet never paid before (a change, or the first one) is held until the supplier proves it
controls it by signing a challenge. Then a person, who knows whether the supplier really asked
for it, is asked to approve. The signature catches typos and wrong addresses; the person catches
impersonation.
"""

import re

from embco.controls.base import Finding, ask, hold, passed, short
from embco.controls.confirmations import WalletProof
from embco.controls.context import Context
from embco.ledger.models import PaymentRecord, Supplier

_EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


def is_evm_address(value: str) -> bool:
    return bool(_EVM_ADDRESS.match(value))


def last_paid_wallet(payments: tuple[PaymentRecord, ...] | list[PaymentRecord]) -> str | None:
    paid = [p.payee_wallet for p in payments if p.payee_wallet]
    return paid[-1] if paid else None


def needs_proof(supplier: Supplier, last_paid: str | None, proof: WalletProof | None) -> bool:
    """True when the wallet on file is valid, was never paid, and has no matching proof."""
    wallet = supplier.wallet_address
    if not wallet or not is_evm_address(wallet):
        return False
    if last_paid and last_paid.lower() == wallet.lower():
        return False
    return proof is None or proof.wallet.lower() != wallet.lower()


class PayeeWallet:
    name = "payee_wallet"

    def check(self, ctx: Context) -> Finding:
        wallet = ctx.supplier.wallet_address
        if ctx.supplier.wallet_problem:
            return hold(self.name, ctx.supplier.wallet_problem)
        if not wallet:
            return hold(self.name, "the supplier has no wallet on file")
        if not is_evm_address(wallet):
            return hold(self.name, "the wallet on file is not a valid address")
        last = last_paid_wallet(ctx.payments)
        if last and last.lower() == wallet.lower():
            return passed(self.name, "wallet matches the one paid last time")
        what = (
            f"wallet changed since the last payment ({short(last)} to {short(wallet)})"
            if last
            else f"first payment to this supplier, wallet {short(wallet)}"
        ) + _who_changed(ctx, wallet)
        proof = ctx.wallet_proof
        if proof is None or needs_proof(ctx.supplier, last, proof):
            return hold(self.name, f"{what}; waiting for the supplier to sign the wallet challenge")
        return ask(
            self.name,
            f"{what}; the supplier proved it controls the new wallet on "
            f"{proof.signed_at:%Y-%m-%d}. Approve only if the supplier asked for this change "
            "(check on the number you already have)",
        )


def _who_changed(ctx: Context, wallet: str) -> str:
    """From the ERP's change history: who set the wallet now on file, and when."""
    setting = [c for c in ctx.wallet_changes if (c.new or "").lower() == wallet.lower()]
    if not setting:
        return ""
    latest = setting[0]
    when = f"{latest.changed_at:%Y-%m-%d %H:%M}"
    return f" (set in the ERP by {latest.changed_by} on {when}, ERP time)"
