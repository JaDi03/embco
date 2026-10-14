"""The owner's Unified Balance as the agent uses it. The agent chooses how much to bring and from
which chains; where the money goes is not its choice: always the owner's own wallet on Arc, the
one the shop contract pays from."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from services.payments import Payer
from services.unified_balance.client import Allocation, ChainFunds, UnifiedBalance


@dataclass
class OwnerFunds:
    kit: UnifiedBalance
    payer: Payer  # knows the shop contract (whose owner receives) and the agent's wallet

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
