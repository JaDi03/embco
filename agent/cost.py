"""What a session cost, estimated from its tokens and the public price list.

Prices per million tokens, for prompts up to 100K tokens: Claude Haiku 5.5 $0.10 in, $0.50 out.
Cache reads cost 0.1x the input price and 5-minute cache writes 1.25x. A model not listed has no
estimate rather than a wrong one.
"""

from decimal import Decimal

from agent.models import Usage

PER_MILLION = {  # model: (input, output) in USD per million tokens
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
}
CACHE_READ = Decimal("0.1")
CACHE_WRITE = Decimal("1.25")
MILLION = Decimal(1_000_000)


def estimate(usage: Usage, model: str) -> Decimal | None:
    prices = PER_MILLION.get(model)
    if prices is None:
        return None
    inp, out = prices
    total = (usage.input_tokens * inp + usage.output_tokens * out
             + usage.cache_read_tokens * inp * CACHE_READ
             + usage.cache_write_tokens * inp * CACHE_WRITE)
    return total / MILLION


def shown(cost: Decimal | None) -> str:
    """$0.006 style, never in scientific notation; empty when unknown."""
    if cost is None:
        return ""
    return f"${cost.quantize(Decimal('0.0001')).normalize():f}"
