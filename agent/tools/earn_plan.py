"""The numbers the agent weighs before it puts idle USDC in Earn, worked out in one place.

Pure arithmetic over what the toolbox read: the owner's ShopEarn and vaults on Arc, the vaults'
APY from Circle's Earn Kit, the owner's Gateway balance and wallets on other chains, the
Gateway's real fee estimate for each chain, and the bills that need money soon. It decides
nothing: the model reads the result and chooses.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from agent.guardrails.controls.base import fmt

DAYS_PER_YEAR = Decimal(365)
MONTH = Decimal(30)
CENT = Decimal("0.01")


@dataclass(frozen=True)
class ChainRoute:
    """What the agent can reach on one other chain, and what bringing it to Arc costs."""

    chain: str
    in_gateway: Decimal  # the owner's Gateway balance there the agent may spend
    may_deposit: Decimal  # from the owner's wallet there, within ShopFunding's limits
    fees_on_top: Decimal  # gas and provider fee, taken from that chain's balance
    forwarder_fee: Decimal  # taken off what is minted on Arc
    estimate_error: str = ""
    in_owner_wallet: Decimal = Decimal(0)  # all of it, also what the limits keep out of reach


def _percent(apy: Decimal | None) -> str:
    return "unknown" if apy is None else f"{fmt((apy * 100).quantize(CENT))}%"


def best_vault(vaults: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The approved vault that takes deposits with the highest APY known."""
    open_ = [v for v in vaults if v["approved"] and v["accepts_deposits"] and v["apy"] is not None
             and v.get("status", "active") == "active"]
    return max(open_, key=lambda v: v["apy"]) if open_ else None


def yield_numbers(amount: Decimal, apy: Decimal | None, fee: Decimal) -> dict[str, str]:
    """What `amount` earns at `apy`, and how long until it has paid back `fee`."""
    if apy is None or apy <= 0 or amount <= 0:
        return {"earns_per_day": "unknown", "earns_in_30_days_after_fees": "unknown",
                "days_to_pay_back_fees": "unknown"}
    per_day = amount * apy / DAYS_PER_YEAR
    return {
        "earns_per_day": fmt(per_day.quantize(Decimal("0.000001"))),
        "earns_in_30_days_after_fees": fmt((per_day * MONTH - fee).quantize(Decimal("0.000001"))),
        "days_to_pay_back_fees": fmt((fee / per_day).quantize(CENT)) if fee > 0 else "0",
    }


def earn_plan(*, arc_wallet: Decimal, cushion: Decimal, reserve: Decimal, park_room: Decimal,
              max_per_park: Decimal, vaults: list[dict[str, Any]], routes: list[ChainRoute],
              strategy: str, blocked: str = "") -> dict[str, Any]:
    """Each place idle USDC could come from, how much may go to Earn from it now, the fee, the
    yield and the steps. Amounts never exceed what the owner's limits allow this week."""
    vault = best_vault(vaults)
    apy = vault["apy"] if vault else None
    room = park_room
    arc_idle = max(Decimal(0), arc_wallet - cushion - reserve)
    from_arc = min(arc_idle, room)
    room -= from_arc
    sources: list[dict[str, Any]] = [{
        "from": "the owner's wallet on Arc",
        "idle": fmt(arc_idle),
        "can_go_to_earn_now": fmt(from_arc),
        "fees": "0",
        **yield_numbers(from_arc, apy, Decimal(0)),
        "steps": ["park_in_earn"] if from_arc > 0 else [],
        "ready": "now",
    }]
    for r in routes:
        reachable = r.in_gateway + r.may_deposit
        if reachable <= 0 and r.in_owner_wallet <= 0:
            continue
        fee = r.fees_on_top + r.forwarder_fee
        bring = max(Decimal(0), reachable - r.fees_on_top)  # the fee comes out of the same balance
        arrives = max(Decimal(0), bring - r.forwarder_fee)
        invest = min(arrives, room)
        room -= invest
        steps = []
        if r.may_deposit > 0:
            steps += [f"deposit_to_gateway {r.chain} {fmt(r.may_deposit)}",
                      "set an alarm about 15 minutes ahead (that chain must be final)"]
        steps += [f"estimate_bring_funds then bring_funds {r.chain}:{fmt(bring)}",
                  f"park_in_earn {fmt(invest)}"]
        sources.append({
            "from": f"{r.chain} (Gateway {fmt(r.in_gateway)}, plus {fmt(r.may_deposit)} the "
                    "agent may deposit from the owner's wallet)",
            "reachable": fmt(reachable),
            "in_owner_wallet_there": fmt(r.in_owner_wallet),
            "out_of_reach_by_the_owner_limits": fmt(max(Decimal(0),
                                                        r.in_owner_wallet - r.may_deposit)),
            "fees": fmt(fee) if not r.estimate_error else f"unknown: {r.estimate_error}",
            "arrives_on_arc": fmt(arrives),
            "can_go_to_earn_now": fmt(invest),
            **yield_numbers(invest, apy, fee),
            "steps": steps if invest > 0 else [],
            "ready": "about 15 minutes" if r.may_deposit > 0 else "now",
        })
    return {
        "earn_blocked": blocked or None,
        "best_vault": None if vault is None else {
            "vault": vault["key"], "name": vault["name"],
            "apy": _percent(vault["apy"])},
        "vaults": [{"vault": v["key"], "name": v["name"],
                    "apy": _percent(v["apy"]),
                    "approved_by_owner": v["approved"], "accepts_deposits": v["accepts_deposits"],
                    "in_it_now": fmt(v["balance"])} for v in vaults],
        "kept_for_bills_due_within_7_days": fmt(reserve),
        "kept_in_wallet_for_arc_fees": fmt(cushion),
        "may_park_this_week": fmt(park_room),
        "max_per_park": fmt(max_per_park),
        "liquidity_strategy": strategy,
        "sources": sources,
        "how_to_read": "Fees are real estimates, the APY is Circle's. Moving from another chain "
                       "costs about the same whatever the amount, so small amounts take long to "
                       "pay back. Money out_of_reach_by_the_owner_limits needs the owner to "
                       "raise them. Weigh days_to_pay_back_fees against how long the money can "
                       "stay, then decide. Park in pieces of at most max_per_park.",
    }
