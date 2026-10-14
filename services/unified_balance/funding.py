"""The owner's USDC in their own wallets on other chains, and the ShopFunding contract there that
lets the agent move it into the owner's Gateway balance, within limits only the owner sets.

Reads go to each chain's public node, as Circle's kit defines it. A move is the agent's Circle
wallet calling ShopFunding.fund on that chain, simulated first and sent with an idempotency key.
The contract only ever deposits for the owner, so the money stays the owner's.
"""

import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

from services.circle import CircleClient, CircleError
from services.payments import ArcRpc, ChainError
from services.payments.encoding import USDC_DECIMALS, usdc_units
from services.unified_balance.client import ChainFacts

# ShopFundingFactory on each source chain (contracts/src/ShopFundingFactory.sol); a chain without
# one is not offered.
FACTORIES: dict[str, str] = {"Base_Sepolia": "0x69e31Af302Ee222204AA78099D39a2956F5C95d6",
                             "Ethereum_Sepolia": "0x69e31Af302Ee222204AA78099D39a2956F5C95d6"}
FOLLOW_SECONDS = 90  # how long a move is followed before it is reported as sent
POLL_SECONDS = 3


def _usdc(units: int) -> Decimal:
    return Decimal(units).scaleb(-USDC_DECIMALS)


@dataclass(frozen=True)
class WalletFunds:
    """The owner's USDC on one chain and how much of it the agent may move into Gateway now."""

    chain: str
    in_wallet: Decimal
    funding: str | None  # the owner's ShopFunding there; None until the owner creates it
    approved: Decimal = Decimal(0)  # what the owner lets it take
    max_per_move: Decimal = Decimal(0)
    left_this_week: Decimal = Decimal(0)
    paused: bool = False
    agent_is_set: bool = False

    @property
    def can_move(self) -> Decimal:
        if not self.funding or self.paused or not self.agent_is_set:
            return Decimal(0)
        return min(self.in_wallet, self.approved, self.max_per_move, self.left_this_week)


class OwnerWallets:
    def __init__(self, circle: CircleClient, facts: Callable[[], list[ChainFacts]], *,
                 factories: Mapping[str, str] = FACTORIES,
                 node: Callable[[str], ArcRpc] = ArcRpc,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._circle = circle
        self._facts = facts
        self._factories = {chain: f for chain, f in factories.items() if f}
        self._node = node
        self._sleep = sleep

    def _chains(self) -> list[ChainFacts]:
        return [c for c in self._facts() if c.chain in self._factories]

    def read(self, owner: str, agent: str) -> list[WalletFunds]:
        return [self._read(c, owner, agent) for c in self._chains()]

    def _read(self, chain: ChainFacts, owner: str, agent: str) -> WalletFunds:
        rpc = self._node(chain.rpc)
        view = lambda to, sig, types=(), args=(): rpc.call(  # noqa: E731
            to, keccak(text=sig)[:4] + encode(list(types), list(args)))
        (in_wallet,) = decode(["uint256"], view(chain.usdc, "balanceOf(address)",
                                                ["address"], [owner]))
        (fundings,) = decode(["address[]"], view(self._factories[chain.chain],
                                                 "fundingsOf(address)", ["address"], [owner]))
        if not fundings:
            return WalletFunds(chain.chain, _usdc(in_wallet), None)
        funding = to_checksum_address(fundings[-1])
        (approved,) = decode(["uint256"], view(chain.usdc, "allowance(address,address)",
                                               ["address", "address"], [owner, funding]))
        (per_move,) = decode(["uint256"], view(funding, "maxPerMove()"))
        (left,) = decode(["uint256"], view(funding, "remainingThisWeek()"))
        (paused,) = decode(["bool"], view(funding, "paused()"))
        (its_agent,) = decode(["address"], view(funding, "agent()"))
        return WalletFunds(chain.chain, _usdc(in_wallet), funding, _usdc(approved),
                           _usdc(per_move), _usdc(left), paused,
                           to_checksum_address(its_agent) == to_checksum_address(agent))

    def deposit(self, owner: str, agent: str, chain: str, amount: Decimal) -> dict[str, Any]:
        """Move `amount` of the owner's USDC on `chain` into the owner's Gateway balance."""
        facts = next((c for c in self._chains() if c.chain == chain), None)
        if facts is None:
            raise ChainError(f"no ShopFunding factory on {chain}; chains offered: "
                             f"{', '.join(self._factories) or 'none yet'}")
        funds = self._read(facts, owner, agent)
        if funds.funding is None:
            raise ChainError(f"the owner has not set up {chain} yet (dashboard: USDC on other "
                             "chains)")
        call = keccak(text="fund(uint256)")[:4] + encode(["uint256"], [usdc_units(amount)])
        self._node(facts.rpc).call(funds.funding, call, sender=agent)  # reverts say why
        wallet = self._circle.wallet_on(agent, facts.circle)
        sent = self._circle.execute_contract(wallet.id, funds.funding, call,
                                             idempotency_key=str(uuid.uuid4()))
        waited = 0
        while not sent.finished and waited < FOLLOW_SECONDS:
            self._sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            sent = self._circle.get_transaction(sent.id)
        if sent.finished and sent.state != "COMPLETE":
            raise CircleError(f"the move on {chain} {sent.state.lower()}: "
                              f"{sent.error_reason or 'no reason given'}")
        return {"chain": chain, "amount": str(amount), "state": sent.state.lower(),
                "tx": sent.tx_hash or "", "circle_tx": sent.id}
