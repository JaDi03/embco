"""What the hub reads on chain: which shops a wallet owns, and a shop's limits and agent."""

from dataclasses import dataclass
from decimal import Decimal

from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

from embco.payments.chain import ArcRpc
from embco.payments.encoding import USDC_DECIMALS

DEFAULT_FACTORY = "0x4d8efEc867e9F46c05E8359e81dEC6C7D13c95A8"  # ShopPayablesFactory, Arc testnet


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

    def _view(self, to: str, signature: str, types: list[str], args: list) -> bytes:
        return self._rpc.call(to, keccak(text=signature)[:4] + encode(types, args))


def _usdc(units: int) -> Decimal:
    """Exact, and written plainly: 300, not 3E+2."""
    return Decimal(format(Decimal(units).scaleb(-USDC_DECIMALS).normalize(), "f"))
