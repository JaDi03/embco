"""Factories used by the dashboard, their event signatures, and read-only nodes."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from eth_utils import keccak, to_checksum_address

from services.earn.client import EARN_FACTORY
from services.hub.chain import DEFAULT_FACTORY
from services.network import TESTNET
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
    """Testnet only: the history comes from the explorer's API, which Arc mainnet's explorer
    does not answer to scripts."""
    arc = env.get(TESTNET.rpc_env, "").strip()
    if not arc:
        raise ValueError(f"missing {TESTNET.rpc_env}")
    return [
        Factory(TESTNET.chain_id,
                to_checksum_address(env.get("EMBCO_FACTORY_ADDRESS") or DEFAULT_FACTORY),
                "ShopPayables", "ShopCreated(address,address,address,uint256,uint256)",
                arc, TESTNET.explorer),
        Factory(TESTNET.chain_id, EARN_FACTORY, "ShopEarn",
                "EarnCreated(address,address,address,uint256,uint256)",
                arc, TESTNET.explorer),
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
