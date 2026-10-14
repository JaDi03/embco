"""Each shop gets its own Circle wallets for its agent, so one contract's limits and brake cover
exactly one shop.

A new shop gets two, made before its contract so the contract is created with its agent: a smart
contract account (SCA) that is the agent of the contract and sends every transaction, its gas
paid by Circle Gas Station on every chain; and an EOA in the same wallet set that only signs the
owner's Unified Balance spends as their delegate, which an SCA cannot sign. Older shops have one
EOA that does both and pays its own gas.
"""

import uuid
from dataclasses import dataclass

from services.circle import ARC_TESTNET, CircleClient, CircleError, CircleWallet

_NAMESPACE = uuid.UUID("5b0c7e0e-6a43-4f43-9d8e-6d2f0b1f2a10")  # fixed: same shop, same keys


def wallet_names(shop: str) -> tuple[str, str]:
    """Short names: Circle refused a 53-character wallet set name with "API parameter invalid".
    The full address is in the shop's settings; the first bytes are enough to recognise it."""
    tag = shop[:10]
    return f"embco {tag}", f"embco agent {tag}"


def create_agent_wallet(circle: CircleClient, shop: str) -> CircleWallet:
    """Idempotency keys come from the shop, so a retry does not make a second wallet."""
    set_name, wallet_name = wallet_names(shop)
    wallet_set = circle.create_wallet_set(
        set_name, idempotency_key=str(uuid.uuid5(_NAMESPACE, f"set:{shop}")))
    return circle.create_eoa_wallet(
        wallet_set, wallet_name, idempotency_key=str(uuid.uuid5(_NAMESPACE, f"wallet:{shop}")))


@dataclass(frozen=True)
class AgentWallets:
    agent: CircleWallet  # the agent of the contract
    signer: CircleWallet  # signs Unified Balance spends; the agent itself in older shops


def create_agent_wallets(circle: CircleClient, owner: str, index: int) -> AgentWallets:
    """The wallets for the owner's shop number `index` (0 for their first). Idempotency keys come
    from both, so asking again before the contract exists returns the same two wallets."""
    owner = owner.lower()
    key = lambda part: str(uuid.uuid5(_NAMESPACE, f"{part}:{owner}:{index}"))  # noqa: E731
    tag = f"{owner[:10]} #{index + 1}"
    wallet_set = circle.create_wallet_set(f"embco {tag}", idempotency_key=key("set"))
    agent = circle.create_wallet(wallet_set, f"embco agent {tag}", account_type="SCA",
                                 idempotency_key=key("agent"))
    signer = circle.create_wallet(wallet_set, f"embco signer {tag}", account_type="EOA",
                                  idempotency_key=key("signer"))
    return AgentWallets(agent=agent, signer=signer)


def agent_wallets_of(circle: CircleClient, agent: str) -> AgentWallets | None:
    """Our wallets behind the agent of a contract; None when it is not one of ours."""
    wallet = circle.find_wallet(agent, ARC_TESTNET)
    if wallet is None:
        return None
    if wallet.account_type != "SCA":
        return AgentWallets(agent=wallet, signer=wallet)
    signers = [w for w in circle.wallets_in_set(wallet.wallet_set_id)
               if w.account_type == "EOA"]
    if len(signers) != 1:
        raise CircleError(f"the agent's wallet set has {len(signers)} signers, expected 1")
    return AgentWallets(agent=wallet, signer=signers[0])
