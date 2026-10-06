"""Policy numbers. They are declared, never inferred, and they live outside the model."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PolicyConfig:
    max_per_payment: Decimal
    weekly_budget: Decimal
    max_price_increase: Decimal = Decimal("0.15")
    rate_tolerance: Decimal = Decimal(0)
