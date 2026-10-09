"""What the owner does about the agent's questions, from the dashboard: answer one, or ask the
agent to look again now instead of at its next scheduled check.

An answer is tied to the exact question the owner saw (its fingerprint) and left in the shop's
inbox; the shop's agent records it on its next cycle and refuses it if the question changed.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Cookie
from pydantic import BaseModel, Field

from embco.decision import Verdict
from embco.hub.errors import HubError
from embco.hub.units import UnitError, Units
from embco.shops.inbox import answer_waiting, drop_answer
from embco.shops.store import ShopStore

CHECK_EVERY = timedelta(seconds=30)  # "check now" restarts the agent: not more often than this


class Answer(BaseModel):
    invoice: str = Field(min_length=1, max_length=140)
    verdict: Verdict
    fingerprint: str = Field(min_length=64, max_length=64)
    note: str = Field(default="", max_length=500)


def waiting_answers(store: ShopStore, shop: str, last_run: dict[str, Any] | None) -> list[str]:
    """Invoices whose answer the agent has not read yet."""
    decisions = (last_run or {}).get("decisions") or []
    return [d["invoice"] for d in decisions if answer_waiting(store.folder(shop), d["invoice"])]


def owner_action_routes(
    *,
    store: ShopStore,
    units: Units,
    shop_of: Callable[[str], str],
    owner_session: Callable[[str, str | None], Any],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter()
    last_check: dict[str, datetime] = {}

    @router.post("/api/shops/{shop}/answers")
    def answer(shop: str, body: Answer,
               embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        shop = shop_of(shop)
        session = owner_session(shop, embco_session)
        decisions = (store.last_run(shop) or {}).get("decisions") or []
        shown = next((d for d in decisions if d.get("invoice") == body.invoice), None)
        if shown is None or shown.get("action") != "ASK":
            raise HubError(409, "the agent is not asking about this invoice now; refresh")
        if shown.get("fingerprint") != body.fingerprint:
            raise HubError(409, "the agent's question changed; refresh and look again")
        drop_answer(store.folder(shop), body.invoice, body.verdict, body.fingerprint,
                    f"owner {session.owner}", body.note.strip(), clock())
        return {"invoice": body.invoice, "received": True}

    @router.post("/api/shops/{shop}/check")
    def check_now(shop: str,
                  embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        shop = shop_of(shop)
        owner_session(shop, embco_session)
        now = clock()
        if shop in last_check and now - last_check[shop] < CHECK_EVERY:
            raise HubError(429, "the agent is already checking; wait a few seconds")
        if shop not in store.connected():
            raise HubError(409, "connect the ERP first")
        try:
            units.start(shop)  # a restart: the agent checks right away
        except UnitError as error:
            raise HubError(500, "the agent did not restart; try again") from error
        last_check[shop] = now
        return {"checking": True}

    return router
