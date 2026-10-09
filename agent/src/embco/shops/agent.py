"""The agent of one shop: its own ERP connection, its own memory, its own loop.

Each shop runs in its own process (`embco shop --dir <root>/<shop>`), so a shop that fails or
stops does not touch another. After every cycle it leaves a summary for the dashboard.
"""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from embco.circle import CircleClient
from embco.decision import PolicyConfig
from embco.hub.chain import DEFAULT_FACTORY, ShopChain, ShopLimits
from embco.journal import JournalError, SqliteJournal
from embco.ledger import ErpnextAdapter, LedgerError
from embco.payments import ArcRpc, ChainError, Payer, PaymentEvent
from embco.runner import CycleReport, format_report, run_cycle, watch
from embco.settings import Settings
from embco.shops import activity
from embco.shops.inbox import take_answers, take_signatures
from embco.shops.owner_view import decision_view
from embco.shops.store import ShopStore
from embco.shops.supplier_view import supplier_view

log = logging.getLogger("embco")


def erp_for(settings: Settings) -> ErpnextAdapter:
    return ErpnextAdapter(
        settings.erpnext_url,
        settings.erpnext_api_key,
        settings.erpnext_api_secret,
        company=settings.company,
        paid_from=settings.erpnext_paid_from,
        payment_extra=settings.erpnext_payment_extra,
        wallet_bank=settings.erpnext_wallet_bank,
        draft_payments=settings.erpnext_draft_payments,
    )


def payer_for(settings: Settings, ledger: ErpnextAdapter) -> Payer | None:
    if not settings.pay:
        return None
    return Payer(
        ledger=ledger,
        chain=ArcRpc(settings.arc_rpc_url),
        circle=CircleClient(settings.circle_api_key, settings.circle_entity_secret),
        shop=settings.shop_address,
        wallet_id=settings.agent_wallet_id,
        writer=ledger,
    )


def limits_for(settings: Settings) -> Callable[[str], ShopLimits] | None:
    """The contract's limits, read on chain when the shop has the Arc node."""
    if not settings.arc_rpc_url:
        return None
    return ShopChain(ArcRpc(settings.arc_rpc_url), DEFAULT_FACTORY).limits


def current_policy(policy: PolicyConfig, limits: Callable[[str], ShopLimits] | None,
                   shop: str) -> PolicyConfig:
    """The owner changes limits in the contract, so the agent reads them before deciding and
    decides with the same limits the contract will enforce. If the node does not answer, the
    last known limits stay."""
    if limits is None:
        return policy
    try:
        current = limits(shop)
    except ChainError as error:
        log.warning("shop %s: contract limits not read, keeping the last ones: %s", shop, error)
        return policy
    return replace(policy, max_per_payment=current.max_per_payment,
                   weekly_budget=current.weekly_cap)


def summarize(
    report: CycleReport,
    payments: Mapping[str, PaymentEvent] | None = None,
    answers: Mapping[str, dict[str, str]] | None = None,
    policy: PolicyConfig | None = None,
) -> dict[str, Any]:
    plan = report.plan
    payments = payments or {}
    return {
        "ok": True,
        "run_id": report.run_id,
        "at": report.at.isoformat(),
        "counts": {"pay": len(plan.pay_now), "deferred": len(plan.deferred),
                   "held": len(plan.held), "ask": len(plan.asked)},
        "budget_left": str(plan.budget_left),
        "limits": None if policy is None else {
            "max_per_payment": str(policy.max_per_payment),
            "weekly_cap": str(policy.weekly_budget)},
        "decisions": [decision_view(d, payments.get(d.invoice)) for d in report.decisions],
        "answers": dict(answers or {}),
    }


def run_shop(
    store: ShopStore,
    shop: str,
    *,
    cycles: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    erp: Callable[[Settings], ErpnextAdapter] = erp_for,
    platform: Mapping[str, str] | None = None,
    payer: Callable[[Settings, ErpnextAdapter], Payer | None] = payer_for,
    limits: Callable[[Settings], Callable[[str], ShopLimits] | None] = limits_for,
) -> int:
    """Returns how many cycles ran without an ERP error."""
    settings = store.settings(shop, platform)
    ledger = erp(settings)
    payments = payer(settings, ledger)
    log.info("shop %s: watching %s every %s (payments %s)", shop, settings.company,
             settings.interval, "on, testnet drafts in the ERP" if payments else "off")
    signatures: dict[str, dict[str, str]] = {}  # the last outcome per supplier, for its page
    answers: dict[str, dict[str, str]] = {}  # the last outcome per invoice, for the owner
    read_limits = limits(settings)
    policy = {"now": settings.policy}

    def wallet_of(supplier: str) -> str | None:
        try:
            return ledger.get_supplier(supplier).wallet_address
        except LedgerError:
            return None  # the supplier cannot sign in to its page until the ERP answers

    with SqliteJournal(settings.journal_path) as journal:

        folder = store.folder(shop)
        minutes = int(settings.interval.total_seconds() // 60)
        seen: dict[str, PolicyConfig | None] = {"limits": None}

        def cycle() -> None:
            started = datetime.now(UTC)
            activity.append_activity(folder, [activity.check_started(started)])
            try:
                signed = take_signatures(folder, journal, ledger)
                answered = take_answers(folder, journal)
                signatures.update(signed)
                answers.update(answered)
                policy["now"] = current_policy(policy["now"], read_limits, shop)
                activity.append_activity(folder, [
                    *activity.inbox_events(started, signed, answered),
                    *(activity.limits_read(started, seen["limits"], policy["now"])
                      if read_limits else [])])
                seen["limits"] = policy["now"]
                report = run_cycle(ledger, journal, policy["now"], settings.company,
                                   payments=payments)
            except (LedgerError, JournalError) as error:
                store.write_last_run(shop, {"ok": False, "at": datetime.now(UTC).isoformat(),
                                            "error": str(error)})
                activity.append_activity(folder, [activity.check_failed(datetime.now(UTC), error)])
                raise
            activity.append_activity(folder, activity.cycle_events(report, minutes))
            latest = {p.invoice: p for p in journal.latest_payments()}
            store.write_last_run(shop, summarize(report, latest, answers, policy["now"]))
            store.write_supplier_view(shop, supplier_view(report, journal, settings.company,
                                                          signatures, wallet_of=wallet_of))
            log.info("shop %s: %s", shop, format_report(report))

        return watch(cycle, settings.interval, cycles=cycles, sleep=sleep)
