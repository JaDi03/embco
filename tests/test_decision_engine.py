from datetime import date
from decimal import Decimal

from agent.guardrails.rules import Action, DecisionEngine, PolicyConfig, plan_payments
from support import WALLET_B, FakeLedger, make_invoice

CONFIG = PolicyConfig(max_per_payment=Decimal(5000), weekly_budget=Decimal(2500))


def decide(ledger: FakeLedger):
    return DecisionEngine(ledger, CONFIG).decide_all()[0]


def test_a_clean_invoice_is_paid():
    decision = decide(FakeLedger())
    assert decision.action is Action.PAY
    assert decision.reasons == ("all 6 controls passed",)


def test_a_wallet_change_holds_the_payment_with_a_reason():
    ledger = FakeLedger()
    ledger.supplier = ledger.supplier.model_copy(update={"wallet_address": WALLET_B})
    decision = decide(ledger)
    assert decision.action is Action.HOLD
    assert any("wallet changed" in reason for reason in decision.reasons)


def test_a_price_spike_asks_the_owner():
    ledger = FakeLedger()
    ledger.orders["PO-1"] = ledger.orders["PO-1"].model_copy(update={"lines": (
        ledger.orders["PO-1"].lines[0].model_copy(update={"rate": Decimal(135)}),)})
    ledger.pending = [make_invoice(rate="135")]
    assert decide(ledger).action is Action.ASK


def test_hold_wins_over_ask():
    ledger = FakeLedger()
    ledger.supplier = ledger.supplier.model_copy(update={"wallet_address": WALLET_B})
    ledger.pending = [make_invoice(qty="100", rate="100")]  # above the limit and a wallet change
    decision = decide(ledger)
    assert decision.action is Action.HOLD
    assert len(decision.reasons) >= 2


def test_the_plan_pays_the_most_urgent_first_and_defers_what_does_not_fit():
    ledger = FakeLedger()
    ledger.pending = [
        make_invoice("PINV-A", "A", due_date=date(2026, 10, 20)),
        make_invoice("PINV-B", "B", due_date=date(2026, 10, 5)),
        make_invoice("PINV-C", "C", due_date=date(2026, 10, 12)),
    ]
    plan = plan_payments(DecisionEngine(ledger, CONFIG).decide_all(), CONFIG.weekly_budget)
    assert [d.invoice for d in plan.pay_now] == ["PINV-B", "PINV-C"]
    assert [d.decision.invoice for d in plan.deferred] == ["PINV-A"]
    assert plan.budget_left == Decimal(500)
