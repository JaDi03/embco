"""What the hub reads on chain: which shops a wallet owns, a shop's limits and agent, and how
much USDC a wallet holds. ShopChains does it on every network the server has a node for."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

from services.earn.client import ARC_USDC
from services.network import DEFAULT, TESTNET, Network
from services.payments.chain import ArcRpc, ChainError
from services.payments.encoding import USDC_DECIMALS

DEFAULT_FACTORY = TESTNET.factory  # ShopPayablesFactory, Arc testnet


@dataclass(frozen=True)
class ShopLimits:
    max_per_payment: Decimal
    weekly_cap: Decimal
    agent: str


class ShopChain:
    def __init__(self, rpc: ArcRpc, factory: str) -> None:
        self._rpc = rpc
        self._factory = to_checksum_address(factory)

    def shops_of(self, owner: str) -> list[str]:
        """Shops our factory created for this owner: only those can be managed here."""
        (shops,) = decode(["address[]"], self._view(self._factory, "shopsOf(address)",
                                                    ["address"], [to_checksum_address(owner)]))
        return [s.lower() for s in shops]

    def limits(self, shop: str) -> ShopLimits:
        (per_payment,) = decode(["uint256"], self._view(shop, "maxPerPayment()", [], []))
        (weekly,) = decode(["uint256"], self._view(shop, "weeklyCap()", [], []))
        return ShopLimits(max_per_payment=_usdc(per_payment), weekly_cap=_usdc(weekly),
                          agent=self._rpc.agent_of(shop))

    def usdc_balance(self, wallet: str) -> Decimal:
        """On Arc, USDC is also the gas token: its ERC-20 interface reads the same balance."""
        (units,) = decode(["uint256"], self._view(ARC_USDC, "balanceOf(address)", ["address"],
                                                  [to_checksum_address(wallet)]))
        return _usdc(units)

    def _view(self, to: str, signature: str, types: list[str], args: list) -> bytes:
        return self._rpc.call(to, keccak(text=signature)[:4] + encode(types, args))


class ShopChains:
    """One ShopChain per network. A shop's network is the one in its saved configuration; a
    shop not connected yet is on the network where its contract is deployed."""

    def __init__(self, chains: Mapping[str, tuple[Network, ShopChain, ArcRpc]],
                 saved_network: Callable[[str], str | None] = lambda shop: None) -> None:
        self._chains = dict(chains)
        self._saved = saved_network
        self._found: dict[str, Network] = {}  # contracts do not move: asked once per shop

    @property
    def networks(self) -> list[Network]:
        return [net for net, _chain, _rpc in self._chains.values()]

    def shops_of(self, owner: str) -> list[str]:
        return [s for _net, chain, _rpc in self._chains.values() for s in chain.shops_of(owner)]

    def network_of(self, shop: str) -> Network:
        saved = self._saved(shop)
        if saved:
            if saved not in self._chains:
                raise ChainError(f"this server has no node for the shop's network ({saved})")
            return self._chains[saved][0]
        if shop not in self._found:
            self._found[shop] = next((net for net, _chain, rpc in self._chains.values()
                                      if net is not DEFAULT and rpc.has_code(shop)), DEFAULT)
        return self._found[shop]

    def chain_of(self, shop: str) -> ShopChain:
        return self._chains[self.network_of(shop).name][1]

    def limits(self, shop: str) -> ShopLimits:
        return self.chain_of(shop).limits(shop)


def _usdc(units: int) -> Decimal:
    """Exact, and written plainly: 300, not 3E+2."""
    return Decimal(format(Decimal(units).scaleb(-USDC_DECIMALS).normalize(), "f"))
