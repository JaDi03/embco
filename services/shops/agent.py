"""The agent of one shop: its own ERP connection, its own memory, its own loop.

Each shop runs in its own process (`embco shop --dir <root>/<shop>`), so a shop that fails or
stops does not touch another. After every cycle it leaves a summary for the dashboard.
"""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from agent.cost import estimate, shown
from agent.guardrails.rules import PolicyConfig
from agent.memory import JournalError, SqliteJournal
from agent.models import Autonomy, Wake
from agent.reflexes.cycle import CycleReport, run_cycle
from agent.reflexes.report import format_report
from agent.reflexes.watch import watch
from agent.wiring import brain_setup, room_of
from services.circle import CircleClient
from services.erp import ErpnextAdapter, LedgerError
from services.erp.erpnext.cache import CachedLedger
from services.hub.chain import DEFAULT_FACTORY, ShopChain, ShopLimits
from services.payments import ArcRpc, ChainError, Payer, PaymentEvent
from services.payments.encoding import ref_scope
from services.settings import Settings
from services.shops import activity, chat
from services.shops.inbox import take_answers, take_signatures
from services.shops.owner_view import decision_view
from services.shops.store import ShopStore
from services.shops.supplier_view import supplier_view

log = logging.getLogger("embco")

MESSAGE_POLL = 3  # seconds between looks at the inbox while waiting for the next check


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
        ref_scope=ref_scope(settings.erpnext_url, settings.company),
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


def inbox_wakes(signed: Mapping[str, Mapping[str, str]],
                answered: Mapping[str, Mapping[str, str]], limits_changed: bool) -> list[Wake]:
    """What arrived from the owner and the suppliers since the last cycle wakes the agent."""
    wakes = [Wake("signature", f"{supplier} signed for its wallet "
                               f"({s.get('result', '').lower()}).")
             for supplier, s in signed.items()]
    wakes.extend(Wake("answer", f"The owner answered on {invoice}: "
                                f"{a.get('verdict', '').lower()} ({a.get('result', '').lower()}).",
                      invoice) for invoice, a in answered.items())
    if limits_changed:
        wakes.append(Wake("limits", "The owner changed the limits in the shop contract."))
    return wakes


def chat_replies(report: CycleReport, owner_waiting: bool) -> list[dict[str, Any]]:
    """The agent's answers from this cycle's session, the cost on the last one; or a word from
    the service when the owner is waiting and the session could not answer."""
    session = report.thought.session if report.thought else None
    if session is None:
        return []
    at = session.at.isoformat()
    if session.finished and session.replies:
        cost = shown(estimate(session.usage, session.model))
        replies = [{"from": chat.AGENT, "at": at, "text": r} for r in session.replies]
        replies[-1] |= {"cost": cost, "model": session.model}
        return replies
    if owner_waiting and not session.finished:
        return [{"from": chat.SYSTEM, "at": at, "text": "The agent could not answer right now "
                 f"({session.error}). It tries again in a few minutes."}]
    return []


def claude_today(sessions, zone, now: datetime) -> dict[str, Any]:
    """How many times Claude was woken today (shop time) and what it cost, estimated."""
    today = now.astimezone(zone).date()
    mine = [s for s in sessions if s.at.astimezone(zone).date() == today]
    costs = [estimate(s.usage, s.model) for s in mine]
    known = [c for c in costs if c is not None]
    total = sum(known, start=Decimal(0)) if known else None
    return {"sessions": len(mine), "cost": shown(total)}


def summarize(
    report: CycleReport,
    payments: Mapping[str, PaymentEvent] | None = None,
    answers: Mapping[str, dict[str, str]] | None = None,
    policy: PolicyConfig | None = None,
    today: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = report.plan
    payments = payments or {}
    thought = report.thought
    agent = thought.agent if thought else {}
    session = thought.last if thought else None
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
        "decisions": [decision_view(d, payments.get(d.invoice), agent.get(d.invoice))
                      for d in report.decisions],
        "brain": None if thought is None else {
            "session": None if session is None else {
                "at": session.at.isoformat(), "finished": session.finished,
                "summary": session.summary, "error": session.error},
            "skipped": thought.skipped or None,
            "today": today},
        "answers": dict(answers or {}),
    }


def run_shop(
    store: ShopStore,
    shop: str,
    *,
    cycles: int | None = None,
    sleep: Callable[[float], None] | None = None,
    erp: Callable[[Settings], ErpnextAdapter] = erp_for,
    platform: Mapping[str, str] | None = None,
    payer: Callable[[Settings, ErpnextAdapter], Payer | None] = payer_for,
    limits: Callable[[Settings], Callable[[str], ShopLimits] | None] = limits_for,
) -> int:
    """Returns how many cycles ran without an ERP error."""
    settings = store.settings(shop, platform)
    live = erp(settings)
    # the checks read only what changed; the payer always reads the ERP itself before paying
    ledger = CachedLedger(live) if isinstance(live, ErpnextAdapter) else live
    payments = payer(settings, live)
    log.info("shop %s: watching %s every %s (payments %s)", shop, settings.company,
             settings.interval, "on, testnet drafts in the ERP" if payments else "off")
    signatures: dict[str, dict[str, str]] = {}  # the last outcome per supplier, for its page
    answers: dict[str, dict[str, str]] = {}  # the last outcome per invoice, for the owner
    read_limits = limits(settings)
    policy = {"now": settings.policy}
    brain = brain_setup(settings, room_of(payments))
    if brain:
        log.info("shop %s: agent brain on (%s, %s)", shop, brain.brain.model,
                 brain.autonomy.value)

    def wallet_of(supplier: str) -> str | None:
        try:
            return ledger.get_supplier(supplier).wallet_address
        except LedgerError:
            return None  # the supplier cannot sign in to its page until the ERP answers

    with SqliteJournal(settings.journal_path) as journal:

        folder = store.folder(shop)
        minutes = int(settings.interval.total_seconds() // 60)
        if added := activity.backfill(folder, journal.entries()):
            log.info("shop %s: %d past events added to the activity feed", shop, added)
        seen: dict[str, PolicyConfig | None] = {"limits": None}

        def cycle() -> None:
            if not store.agent_on(shop):
                log.info("shop %s: the owner turned the agent off; nothing runs", shop)
                return
            started = datetime.now(UTC)
            activity.append_activity(folder, [activity.check_started(started)])
            try:
                if isinstance(ledger, CachedLedger):
                    ledger.refresh()
                signed = take_signatures(folder, journal, ledger)
                answered = take_answers(folder, journal)
                signatures.update(signed)
                answers.update(answered)
                policy["now"] = current_policy(policy["now"], read_limits, shop)
                activity.append_activity(folder, [
                    *activity.inbox_events(started, signed, answered),
                    *(activity.limits_read(started, seen["limits"], policy["now"])
                      if read_limits else [])])
                limits_changed = seen["limits"] not in (None, policy["now"])
                seen["limits"] = policy["now"]
                chat.take_messages(folder)
                waiting = chat.unanswered(folder)
                wakes = inbox_wakes(signed, answered, limits_changed)
                if waiting:
                    wakes.append(Wake("message", f"The owner wrote to you ({len(waiting)} "
                                                 "message(s)); answer with reply_owner."))
                thinking = brain
                if brain and store.payments_paused(shop):  # the owner paused payments
                    thinking = replace(brain, autonomy=Autonomy.OBSERVE)
                report = run_cycle(ledger, journal, policy["now"], settings.company,
                                   payments=payments, brain=thinking, wakes=wakes,
                                   messages=[m["text"] for m in waiting],
                                   conversation=chat.read_chat(folder, limit=12))
            except (LedgerError, JournalError) as error:
                store.write_last_run(shop, {"ok": False, "at": datetime.now(UTC).isoformat(),
                                            "error": str(error)})
                activity.append_activity(folder, [activity.check_failed(datetime.now(UTC), error)])
                raise
            activity.append_activity(folder, [*activity.agent_events(report),
                                              *activity.cycle_events(report, minutes)])
            chat.append_chat(folder, chat_replies(report, bool(waiting)))
            latest = {p.invoice: p for p in journal.latest_payments()}
            today = claude_today(journal.sessions(), brain.zone, report.at) if brain else None
            store.write_last_run(shop, summarize(report, latest, answers, policy["now"], today))
            store.write_supplier_view(shop, supplier_view(report, journal, settings.company,
                                                          signatures, wallet_of=wallet_of))
            log.info("shop %s: %s", shop, format_report(report))
            if isinstance(ledger, CachedLedger):
                log.info("shop %s: %d documents read in full this check", shop, ledger.downloads)

        def nap(seconds: float) -> None:
            """Wait for the next check, but wake at once when the owner writes."""
            end = time.monotonic() + seconds
            while (left := end - time.monotonic()) > 0:
                if chat.messages_waiting(folder):
                    return
                time.sleep(min(MESSAGE_POLL, left))

        return watch(cycle, settings.interval, cycles=cycles, sleep=sleep or nap)
