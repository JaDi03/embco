"""The shop owner's Unified Balance (Circle Gateway): USDC the owner keeps on other chains, which
the agent can bring to the owner's wallet on Arc when an invoice needs it. Circle's Unified
Balance Kit does the work; the shop contract still pays every supplier."""

from services.unified_balance.client import (
    Allocation,
    ChainFunds,
    UnifiedBalance,
    UnifiedBalanceError,
)
from services.unified_balance.owner import OwnerFunds

__all__ = ["Allocation", "ChainFunds", "OwnerFunds", "UnifiedBalance", "UnifiedBalanceError"]
