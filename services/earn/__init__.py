"""The shop owner's Earn positions on Arc: idle USDC the agent parks into owner-approved ERC-4626
vaults through the owner's ShopEarn contract, and unparks back to the owner's wallet when invoices
come due. Neither USDC nor vault shares ever sit in the agent or in ShopEarn."""

from services.earn.client import (
    EARN_FACTORY,
    MIN_WALLET_CUSHION,
    VAULTS,
    EarnPosition,
    OwnerEarn,
    ShopEarnState,
    VaultSpec,
)

__all__ = [
    "EARN_FACTORY",
    "MIN_WALLET_CUSHION",
    "VAULTS",
    "EarnPosition",
    "OwnerEarn",
    "ShopEarnState",
    "VaultSpec",
]

