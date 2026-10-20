from decimal import Decimal as D

from agent.tools.earn_plan import ChainRoute, best_vault, earn_plan, yield_numbers

# lopezgrocery on 2026-10-20 22:48 UTC: ShopEarn and vaults read on Arc, APY from Circle's Earn
# Kit, Gateway balances and ShopFunding limits from the shop, fees from Gateway's estimate.
VAULTS = [
    {"key": "morpho", "name": "Morpho USDC Vault", "approved": True, "accepts_deposits": True,
     "balance": D("6.689999"), "apy": D("0.035"), "status": "active"},
    {"key": "earnkit", "name": "EarnKit USDC Vault", "approved": False, "accepts_deposits": False,
     "balance": D(0), "apy": D("0.042"), "status": "active"},
]
SEPOLIA = ChainRoute("Ethereum_Sepolia", D("6.959198"), D("125"), D("1.1"), D("0.015349"),
                     "", D("7415"))


def plan(**overrides):
    args = {"arc_wallet": D("1.004041"), "cushion": D(1), "reserve": D(0),
            "park_room": D("1193.31"), "max_per_park": D(300), "vaults": VAULTS,
            "routes": [SEPOLIA], "strategy": "preserve_yield"}
    return earn_plan(**{**args, **overrides})


def test_the_best_vault_is_the_open_approved_one_not_the_highest_apy():
    assert best_vault(VAULTS)["key"] == "morpho"
    assert plan()["best_vault"] == {"vault": "morpho", "name": "Morpho USDC Vault", "apy": "3.5%"}


def test_a_small_move_from_another_chain_takes_months_to_pay_back_its_fee():
    sepolia = plan()["sources"][1]
    assert sepolia["fees"] == "1.115349"
    assert sepolia["arrives_on_arc"] == "130.843849"
    assert sepolia["days_to_pay_back_fees"] == "88.9"
    assert sepolia["earns_in_30_days_after_fees"].startswith("-")
    assert sepolia["out_of_reach_by_the_owner_limits"] == "7290"
    assert sepolia["steps"][0] == "deposit_to_gateway Ethereum_Sepolia 125"
    assert sepolia["ready"] == "about 15 minutes"


def test_a_large_move_pays_back_its_fee_in_days():
    big = ChainRoute("Ethereum_Sepolia", D("1200"), D(0), D("1.1"), D("0.015"), "", D(0))
    sepolia = plan(routes=[big])["sources"][1]
    assert D(sepolia["days_to_pay_back_fees"]) < 10
    assert sepolia["ready"] == "now"


def test_bills_due_soon_and_the_cushion_stay_on_arc():
    arc = plan(arc_wallet=D(500), reserve=D(300))["sources"][0]
    assert arc["idle"] == "199" and arc["can_go_to_earn_now"] == "199"
    assert arc["fees"] == "0" and arc["days_to_pay_back_fees"] == "0"


def test_nothing_goes_beyond_what_may_be_parked_this_week():
    p = plan(arc_wallet=D(101), park_room=D(60))
    assert p["sources"][0]["can_go_to_earn_now"] == "60"
    assert p["sources"][1]["can_go_to_earn_now"] == "0" and p["sources"][1]["steps"] == []


def test_unknown_apy_is_said_not_guessed():
    assert yield_numbers(D(100), None, D(1))["days_to_pay_back_fees"] == "unknown"
    p = plan(vaults=[{**VAULTS[0], "apy": None}])
    assert p["best_vault"] is None and p["vaults"][0]["apy"] == "unknown"


def test_a_failed_fee_estimate_shows_its_reason():
    broken = ChainRoute("Base_Sepolia", D(50), D(0), D(0), D(0), "kit down", D(0))
    assert plan(routes=[broken])["sources"][1]["fees"] == "unknown: kit down"


def test_the_toolbox_gathers_vaults_apy_gateway_and_fees():
    import json
    from types import SimpleNamespace

    from services.unified_balance import ChainFunds
    from tests.test_earn import MORPHO, make_earn, make_toolbox

    estimates = []

    class Funds:
        def vault_rates(self):
            return [{"vault": MORPHO.lower(), "current_apy": "0.035", "status": "active"}]

        def chains(self):
            return [ChainFunds("Ethereum_Sepolia", D("6.959198"), D(0), "ready")]

        def in_wallets(self):
            return [SimpleNamespace(chain="Ethereum_Sepolia", in_wallet=D(7415), can_move=D(125),
                                    funding="0x4a2c")]

        def estimate(self, allocations):
            estimates.append(allocations)
            return {"fees": [{"type": "gasFee", "amount": "1.093403"},
                             {"type": "provider", "amount": "0.006597"},
                             {"type": "forwarder", "amount": "0.015349"}]}

    tb = make_toolbox(make_earn())
    tb.owner_funds = Funds()
    p = json.loads(tb.call("earn_plan", {}))
    assert p["best_vault"]["apy"] == "3.5%"
    sepolia = p["sources"][1]
    assert sepolia["fees"] == "1.115349" and sepolia["out_of_reach_by_the_owner_limits"] == "7290"
    assert [(a.chain, a.amount) for a in estimates[0]] == [("Ethereum_Sepolia", D("131.959198"))]
