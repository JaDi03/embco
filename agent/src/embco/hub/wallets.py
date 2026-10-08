"""Each shop gets its own Circle wallet for its agent, so one contract's limits and brake cover
exactly one shop. The owner then sets that address as the agent of their contract.
"""

import uuid

from embco.circle import CircleClient, CircleWallet

_NAMESPACE = uuid.UUID("5b0c7e0e-6a43-4f43-9d8e-6d2f0b1f2a10")  # fixed: same shop, same keys


def create_agent_wallet(circle: CircleClient, shop: str) -> CircleWallet:
    """Idempotency keys come from the shop, so a retry does not make a second wallet."""
    wallet_set = circle.create_wallet_set(
        f"embco shop {shop}", idempotency_key=str(uuid.uuid5(_NAMESPACE, f"set:{shop}")))
    return circle.create_eoa_wallet(
        wallet_set, f"embco agent {shop}",
        idempotency_key=str(uuid.uuid5(_NAMESPACE, f"wallet:{shop}")))
