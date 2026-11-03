"""The two Arc networks a shop can run on, and what each one needs from the platform.

A shop runs on one network, chosen when it is created and kept in its config.json. Testnet is
the default, so a shop saved before mainnet existed stays on testnet. Each network has its own
node and its own Circle account (Circle keys belong to one environment), named by the variables
below. On mainnet real USDC moves: every payment is submitted in the ERP, and the testnet-only
extras (Unified Balance funding and Earn) are off.

Chain ids and explorers: Arc docs, "Connect to Arc". Circle blockchain codes: Circle docs,
"Supported blockchains". Contract addresses: contracts/script/Deploy*.s.sol broadcasts.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from eth_utils import to_checksum_address


@dataclass(frozen=True)
class Network:
    name: str
    label: str
    chain_id: int
    circle_blockchain: str
    explorer: str
    rpc_env: str
    circle_key_env: str
    circle_secret_env: str
    factory: str
    journal_anchor: str
    decision_verifier: str
    first_block: int  # the factory's deployment: no shop is older
    log_blocks: int  # the most blocks its node returns events for in one call
    test_money: bool  # test USDC: ERP drafts, Unified Balance funding and Earn

    def platform(self, env: Mapping[str, str]) -> dict[str, str]:
        """This network's node and Circle account, under their variable names."""
        return {name: (env.get(name) or "").strip()
                for name in (self.circle_key_env, self.circle_secret_env, self.rpc_env)}


TESTNET = Network(
    name="testnet",
    label="Arc testnet",
    chain_id=5042002,
    circle_blockchain="ARC-TESTNET",
    explorer="https://explorer.testnet.arc.io",
    rpc_env="ARC_TESTNET_RPC_URL",
    circle_key_env="CIRCLE_API_KEY",
    circle_secret_env="CIRCLE_ENTITY_SECRET",  # noqa: S106 (a variable name, not a credential)
    factory=to_checksum_address("0x5D455dF984C85abe160E4585E0c1139ded5d2c3b"),
    journal_anchor=to_checksum_address("0x4385a2F8acda166F3FE3B3825F6950BA7dAc2582"),
    decision_verifier=to_checksum_address("0x2e23aFE44fe10378F9957030bf3B0d31998dddde"),
    first_block=66943142,
    log_blocks=99_999,  # the Canteen node
    test_money=True,
)

MAINNET = Network(
    name="mainnet",
    label="Arc mainnet",
    chain_id=5042,
    circle_blockchain="ARC",
    explorer="https://explorer.arc.io",
    rpc_env="ARC_MAINNET_RPC_URL",
    circle_key_env="CIRCLE_MAINNET_API_KEY",
    circle_secret_env="CIRCLE_MAINNET_ENTITY_SECRET",  # noqa: S106 (a variable name, not a credential)
    factory=to_checksum_address("0x1304d82964c44E2a8cca4D5bc27E30C83A123a50"),
    journal_anchor=to_checksum_address("0x4d8efEc867e9F46c05E8359e81dEC6C7D13c95A8"),
    decision_verifier=to_checksum_address("0x69e31Af302Ee222204AA78099D39a2956F5C95d6"),
    first_block=29369377,
    log_blocks=10_000,  # rpc.mainnet.arc.io: 10,001 is "requested range too large"
    test_money=False,
)

NETWORKS = {n.name: n for n in (TESTNET, MAINNET)}
DEFAULT = TESTNET


class NetworkError(ValueError):
    pass


def network(name: str | None) -> Network:
    """The network by name; testnet when none was chosen."""
    if not name:
        return DEFAULT
    try:
        return NETWORKS[name]
    except KeyError:
        raise NetworkError(f"network must be one of: {', '.join(NETWORKS)}") from None


def available(env: Mapping[str, str]) -> list[Network]:
    """The networks this server has a node for."""
    return [n for n in NETWORKS.values() if (env.get(n.rpc_env) or "").strip()]
