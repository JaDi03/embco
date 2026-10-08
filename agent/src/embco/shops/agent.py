"""The agent of one shop: its own ERP connection, its own memory, its own loop.

Each shop runs in its own process (`embco shop --dir <root>/<shop>`), so a shop that fails or
stops does not touch another. After every cycle it leaves a summary for the dashboard.
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from embco.journal import JournalError, SqliteJournal
from embco.ledger import ErpnextAdapter, LedgerError
from embco.runner import CycleReport, format_report, run_cycle, watch
from embco.settings import Settings
from embco.shops.store import ShopStore

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
    )


def summarize(report: CycleReport) -> dict[str, Any]:
    plan = report.plan
    return {
        "ok": True,
        "run_id": report.run_id,
        "at": report.at.isoformat(),
        "counts": {"pay": len(plan.pay_now), "deferred": len(plan.deferred),
                   "held": len(plan.held), "ask": len(plan.asked)},
        "budget_left": str(plan.budget_left),
        "decisions": [
            {"invoice": d.invoice, "supplier": d.supplier, "amount": str(d.amount),
             "due_date": d.due_date.isoformat() if d.due_date else None,
             "action": d.action.value, "reasons": list(d.reasons)}
            for d in report.decisions
        ],
    }


def run_shop(
    store: ShopStore,
    shop: str,
    *,
    cycles: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    erp: Callable[[Settings], ErpnextAdapter] = erp_for,
) -> int:
    """Returns how many cycles ran without an ERP error."""
    settings = store.settings(shop)
    ledger = erp(settings)
    log.info("shop %s: watching %s every %s (observing, payments off)", shop, settings.company,
             settings.interval)
    with SqliteJournal(settings.journal_path) as journal:

        def cycle() -> None:
            try:
                report = run_cycle(ledger, journal, settings.policy, settings.company)
            except (LedgerError, JournalError) as error:
                store.write_last_run(shop, {"ok": False, "at": datetime.now(UTC).isoformat(),
                                            "error": str(error)})
                raise
            store.write_last_run(shop, summarize(report))
            log.info("shop %s: %s", shop, format_report(report))

        return watch(cycle, settings.interval, cycles=cycles, sleep=sleep)
