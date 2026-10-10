"""Build the agent's brain from the settings, or None when the brain is off."""

from collections.abc import Callable
from datetime import timezone
from decimal import Decimal

from agent.agent import ClaudeBrain
from agent.guardrails.controls.base import fmt
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


def funds_of(payer: Payer | None) -> Callable[[], dict[str, str] | None] | None:
    """When the contract's weekly room resets, the owner's balance and whether payments are
    authorized, read when the agent asks. None when the node does not answer."""
    if payer is None:
        return None

    def funds() -> dict[str, str] | None:
        try:
            resets = payer.chain.week_resets_at(payer.shop)
            balance, allowed = payer.chain.owner_funds(payer.shop)
            weekly = payer.chain.remaining_this_week(payer.shop)
        except ChainError:
            return None
        usdc = lambda units: fmt(Decimal(units).scaleb(-USDC_DECIMALS))  # noqa: E731
        authorized = ("no" if allowed == 0 else "yes" if allowed >= weekly
                      else f"only {usdc(allowed)} USDC left; the owner must authorize again")
        return {"weekly_room_resets_at_utc": resets.strftime("%Y-%m-%d %H:%M"),
                "owner_balance": usdc(balance), "payments_authorized": authorized,
                "available_to_pay": usdc(min(balance, allowed))}

    return funds


def brain_setup(
    settings: Settings, room: Callable[[], Decimal | None] | None = None,
    funds: Callable[[], dict[str, str] | None] | None = None,
    mailer: Callable[[str, str, str], str] | None = None,
) -> BrainSetup | None:
    if not settings.brain:
        return None
    return BrainSetup(
        brain=ClaudeBrain(settings.brain_model, effort=settings.brain_effort,
                          api_key=settings.anthropic_api_key),
        chat_brain=ClaudeBrain(settings.brain_model, effort="low",
                               api_key=settings.anthropic_api_key),
        zone=timezone(settings.utc_offset),
        round_hour=settings.round_hour,
        autonomy=settings.autonomy,
        daily_tokens=settings.brain_daily_tokens,
        **({"room": room} if room else {}),
        **({"funds": funds} if funds else {}),
        **({"mailer": mailer} if mailer else {}),
    )
