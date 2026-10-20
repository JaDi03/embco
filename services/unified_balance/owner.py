"""The owner's Unified Balance as the agent uses it. The agent chooses how much to bring and from
which chains; where the money goes is not its choice: always the owner's own wallet on Arc, the
one the shop contract pays from."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from services.payments import Payer
from services.unified_balance.client import (
    Allocation,
    ChainFunds,
    UnifiedBalance,
    UnifiedBalanceError,
)
from services.unified_balance.funding import OwnerWallets, WalletFunds


@dataclass
class OwnerFunds:
    kit: UnifiedBalance
    payer: Payer  # knows the shop contract (whose owner receives) and the agent's wallet
    wallets: OwnerWallets | None = None  # the owner's own wallets on other chains
    # The agent's EOA that signs spends as the owner's delegate: a Circle smart contract account
    # cannot sign them. None for a shop whose agent wallet is that EOA itself.
    signer: str | None = None
    _prepared: bool = field(default=False, init=False, repr=False)

    def _owner(self) -> str:
        return self.payer.chain.owner_of(self.payer.shop)

    def _signer(self) -> str:
        return self.signer or self.payer.agent_address()

    def prepare(self) -> list[str]:
        """The agent's Circle wallets on every source chain: the signer signs spends there, and
        the agent wallet moves the owner's USDC into the Unified Balance there."""
        chains = self.kit.prepare(self._signer())
        if self.signer:
            self.kit.prepare(self.payer.agent_address())
        self._prepared = True
        return chains

    def _ready(self) -> None:
        """Prepared at start; when that failed (Circle's rate limit as every shop restarts), again
        before the first move that needs the wallets there. A failure here does not stop the move:
        the wallets usually exist already, and when one does not, the move says so itself."""
        if not self._prepared:
            try:
                self.prepare()
            except UnifiedBalanceError:
                pass

    def chains(self) -> list[ChainFunds]:
        return self.kit.balances(self._owner(), self._signer())

    def estimate(self, allocations: Sequence[Allocation]) -> Any:
        self._ready()
        return self.kit.estimate(self._owner(), self._signer(), allocations)

    def bring(self, allocations: Sequence[Allocation]) -> Any:
        self._ready()
        return self.kit.spend(self._owner(), self._signer(), allocations)

    def vault_rates(self) -> list[dict[str, str]]:
        """Each Earn vault on Arc with its current APY, from Circle's Earn Kit."""
        return self.kit.vaults()

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
        self._ready()
        return self.wallets.deposit(self._owner(), self.payer.agent_address(), chain, amount)
