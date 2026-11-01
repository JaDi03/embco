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


def test_a_price_spike_is_for_the_agent_to_weigh():
    ledger = FakeLedger()
    ledger.orders["PO-1"] = ledger.orders["PO-1"].model_copy(update={"lines": (
        ledger.orders["PO-1"].lines[0].model_copy(update={"rate": Decimal(135)}),)})
    ledger.pending = [make_invoice(rate="135")]
    decision = decide(ledger)
    assert decision.action is Action.PAY
    assert decision.reasons == ("to weigh: COLA: rate 135 is 35% above the habitual 100",)


def test_a_missing_receipt_no_longer_stops_the_invoice_but_a_duplicate_does():
    ledger = FakeLedger()
    bare = make_invoice().lines[0].model_copy(update={"purchase_receipt": None})
    ledger.pending = [make_invoice(lines=(bare,))]
    assert decide(ledger).action is Action.PAY
    ledger.pending = [make_invoice(bill_no="H-0")]  # the bill number of the paid PINV-0
    ledger.history[0] = ledger.history[0].model_copy(update={"posting_date": date(2026, 9, 1)})
    assert decide(ledger).action is Action.HOLD


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


TODAY = date(2026, 10, 6)


def decide_on(ledger: FakeLedger, today=TODAY):
    return DecisionEngine(ledger, CONFIG, today=today).decide_all()[0]


def test_an_invoice_on_hold_in_the_erp_is_held_until_its_release_date():
    ledger = FakeLedger()
    ledger.pending = [make_invoice(on_hold=True)]
    assert decide_on(ledger).reasons == ("the invoice is on hold in the ERP",)
    for release, action in ((date(2026, 10, 7), Action.HOLD), (TODAY, Action.HOLD),
                            (date(2026, 10, 5), Action.PAY)):
        ledger.pending = [make_invoice(on_hold=True, on_hold_until=release)]
        assert decide_on(ledger).action is action, release
    ledger.pending = [make_invoice(on_hold=True, on_hold_until=date(2026, 10, 9))]
    assert decide_on(ledger).reasons == ("the invoice is on hold in the ERP until 2026-10-09",)


def test_payments_on_hold_for_the_supplier_hold_every_invoice_of_it():
    ledger = FakeLedger()
    ledger.supplier = ledger.supplier.model_copy(update={"payments_on_hold": True})
    decision = decide_on(ledger)
    assert decision.action is Action.HOLD
    assert decision.reasons == ("payments to the supplier are on hold in the ERP",)
    ledger.supplier = ledger.supplier.model_copy(update={"on_hold_until": date(2026, 10, 1)})
    assert decide_on(ledger).action is Action.PAY  # released


def test_a_disabled_supplier_is_still_only_weighed():
    ledger = FakeLedger()
    ledger.supplier = ledger.supplier.model_copy(update={"disabled": True})
    decision = decide_on(ledger)
    assert decision.action is Action.PAY
    assert decision.reasons == ("to weigh: the supplier is disabled in the ERP",)
