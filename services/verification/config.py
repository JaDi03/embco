"""Factories used by the dashboard, their event signatures, and read-only nodes."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from eth_utils import keccak, to_checksum_address

from services.earn.client import EARN_FACTORY
from services.hub.chain import DEFAULT_FACTORY
from services.unified_balance.funding import FACTORIES


@dataclass(frozen=True)
class Factory:
    chain: int
    address: str
    contract: str
    event: str
    rpc: str = field(repr=False)
    explorer: str

    @property
    def key(self) -> str:
        return f"{self.chain}:{self.address.lower()}"

    @property
    def topic(self) -> str:
        return "0x" + keccak(text=self.event).hex()


def factories(env: Mapping[str, str]) -> list[Factory]:
    arc = env.get("ARC_TESTNET_RPC_URL", "").strip()
    if not arc:
        raise ValueError("missing ARC_TESTNET_RPC_URL")
    return [
        Factory(5042002, to_checksum_address(env.get("EMBCO_FACTORY_ADDRESS") or DEFAULT_FACTORY),
                "ShopPayables", "ShopCreated(address,address,address,uint256,uint256)",
                arc, "https://explorer.testnet.arc.io"),
        Factory(5042002, EARN_FACTORY, "ShopEarn",
                "EarnCreated(address,address,address,uint256,uint256)",
                arc, "https://explorer.testnet.arc.io"),
        Factory(84532, FACTORIES["Base_Sepolia"], "ShopFunding",
                "FundingCreated(address,address,address,uint256,uint256)",
                env.get("BASE_SEPOLIA_RPC_URL") or "https://sepolia.base.org",
                "https://base-sepolia.blockscout.com"),
        Factory(11155111, FACTORIES["Ethereum_Sepolia"], "ShopFunding",
                "FundingCreated(address,address,address,uint256,uint256)",
                env.get("ETHEREUM_SEPOLIA_RPC_URL") or
                "https://ethereum-sepolia-rpc.publicnode.com",
                "https://eth-sepolia.blockscout.com"),
    ]
