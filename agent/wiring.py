"""Build the agent's brain from the settings, or None when the brain is off."""

from collections.abc import Callable
from datetime import timezone
from decimal import Decimal

from agent.agent import ClaudeBrain
from agent.think import BrainSetup
from services.payments import ChainError, Payer
from services.payments.encoding import USDC_DECIMALS
from services.settings import Settings


def room_of(payer: Payer | None) -> Callable[[], Decimal | None] | None:
    """What the shop contract still lets the agent pay this week, read when the agent asks.
    Unknown (None) when the node does not answer: the payer checks it again anyway."""
    if payer is None:
        return None

    def room() -> Decimal | None:
        try:
            units = payer.chain.remaining_this_week(payer.shop)
        except ChainError:
            return None
        return Decimal(units).scaleb(-USDC_DECIMALS)

    return room


def brain_setup(
    settings: Settings, room: Callable[[], Decimal | None] | None = None
) -> BrainSetup | None:
    if not settings.brain:
        return None
    return BrainSetup(
        brain=ClaudeBrain(settings.brain_model, effort=settings.brain_effort,
                          api_key=settings.anthropic_api_key),
        zone=timezone(settings.utc_offset),
        round_hour=settings.round_hour,
        autonomy=settings.autonomy,
        daily_tokens=settings.brain_daily_tokens,
        **({"room": room} if room else {}),
    )
