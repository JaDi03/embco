"""Payee change control: the wallet we are about to pay must be the one paid last time."""

import re

from embco.controls.base import Finding, ask, hold, passed, short
from embco.controls.context import Context

_EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


class PayeeWallet:
    name = "payee_wallet"

    def check(self, ctx: Context) -> Finding:
        wallet = ctx.supplier.wallet_address
        if not wallet:
            return hold(self.name, "the supplier has no wallet on file")
        if not _EVM_ADDRESS.match(wallet):
            return hold(self.name, "the wallet on file is not a valid address")
        paid = [p.payee_wallet for p in ctx.payments if p.payee_wallet]
        if not paid:
            return ask(self.name, "first payment to this supplier: confirm the wallet")
        last = paid[-1]
        if last.lower() != wallet.lower():
            return hold(
                self.name,
                f"wallet changed since the last payment ({short(last)} to {short(wallet)})",
            )
        return passed(self.name, "wallet matches the one paid last time")
