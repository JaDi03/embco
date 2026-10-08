"""Hosted agents: one folder and one process per shop."""

from embco.shops.agent import erp_for, run_shop, summarize
from embco.shops.store import (
    ErpCredentials,
    ShopConfig,
    ShopError,
    ShopStore,
    check_shop,
    new_key,
    read_key,
)

__all__ = [
    "ErpCredentials",
    "ShopConfig",
    "ShopError",
    "ShopStore",
    "check_shop",
    "erp_for",
    "new_key",
    "read_key",
    "run_shop",
    "summarize",
]
