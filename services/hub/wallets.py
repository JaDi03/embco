"""Each shop gets its own Circle wallet for its agent, so one contract's limits and brake cover
exactly one shop. The owner then sets that address as the agent of their contract.
"""

import uuid

from services.circle import CircleClient, CircleWallet

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
