"""The owner's Unified Balance as the agent uses it. The agent chooses how much to bring and from
which chains; where the money goes is not its choice: always the owner's own wallet on Arc, the
one the shop contract pays from."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from services.payments import Payer
from services.unified_balance.client import Allocation, ChainFunds, UnifiedBalance
from services.unified_balance.funding import OwnerWallets, WalletFunds


@dataclass
class OwnerFunds:
    kit: UnifiedBalance
    payer: Payer  # knows the shop contract (whose owner receives) and the agent's wallet
    wallets: OwnerWallets | None = None  # the owner's own wallets on other chains

    def _owner(self) -> str:
        return self.payer.chain.owner_of(self.payer.shop)

    def prepare(self) -> list[str]:
        """The agent's Circle wallet on every source chain, so it can sign spends there."""
        return self.kit.prepare(self.payer.agent_address())

    def chains(self) -> list[ChainFunds]:
        return self.kit.balances(self._owner(), self.payer.agent_address())

    def estimate(self, allocations: Sequence[Allocation]) -> Any:
        return self.kit.estimate(self._owner(), self.payer.agent_address(), allocations)

    def bring(self, allocations: Sequence[Allocation]) -> Any:
        return self.kit.spend(self._owner(), self.payer.agent_address(), allocations)

    def in_wallets(self) -> list[WalletFunds]:
        """The owner's USDC in their own wallets on other chains, and what the agent may move."""
        if self.wallets is None:
            return []
        return self.wallets.read(self._owner(), self.payer.agent_address())

    def deposit(self, chain: str, amount: Decimal) -> dict[str, Any]:
        """Move the owner's USDC on `chain` into the owner's Gateway balance, through the
        owner's ShopFunding there."""
        if self.wallets is None:
            raise ValueError("the owner's wallets on other chains are not connected")
        return self.wallets.deposit(self._owner(), self.payer.agent_address(), chain, amount)
