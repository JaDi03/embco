"""What the owner does about the agent's questions, from the dashboard: answer one, ask the
agent to look again now instead of at its next scheduled check, and watch what it does.

An answer is tied to the exact question the owner saw (its fingerprint) and left in the shop's
inbox; the shop's agent records it on its next cycle and refuses it if the question changed.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Cookie, Query
from pydantic import BaseModel, Field

from agent.guardrails.rules import Verdict
from services.hub.errors import HubError
from services.hub.units import UnitError, Units
from services.shops import chat
from services.shops.activity import append_activity, read_activity, switched
from services.shops.inbox import answer_waiting, drop_answer
from services.shops.store import ShopError, ShopStore

CHECK_EVERY = timedelta(seconds=30)  # "check now" restarts the agent: not more often than this
MESSAGE_EVERY = timedelta(seconds=3)  # one message at a time from the owner


class Switch(BaseModel):
    on: bool


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=chat.MAX_TEXT)


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
    last_message: dict[str, datetime] = {}

    @router.post("/api/shops/{shop}/messages")
    def message(shop: str, body: Message,
                embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        """The owner writes to the agent; the agent wakes within seconds and answers."""
        shop = shop_of(shop)
        session = owner_session(shop, embco_session)
        text = body.text.strip()
        if not text:
            raise HubError(422, "write something first")
        if shop not in store.connected():
            raise HubError(409, "connect the ERP first")
        if not store.agent_on(shop):
            raise HubError(409, "the agent is off; turn it on to talk to it")
        now = clock()
        if shop in last_message and now - last_message[shop] < MESSAGE_EVERY:
            raise HubError(429, "one message at a time; wait a moment")
        chat.drop_message(store.folder(shop), text, f"owner {session.owner}", now)
        last_message[shop] = now
        return {"received": True}

    @router.get("/api/shops/{shop}/chat")
    def conversation(shop: str, after: int = Query(default=0, ge=0),
                     embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        shop = shop_of(shop)
        owner_session(shop, embco_session)
        folder = store.folder(shop)
        return {"entries": chat.read_chat(folder, after=after),
                "waiting": chat.messages_waiting(folder) or bool(chat.unanswered(folder))}

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

    @router.get("/api/shops/{shop}/activity")
    def activity(shop: str, after: int = Query(default=0, ge=0),
                 embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        """What the agent did since event number `after` (all recent events when 0)."""
        shop = shop_of(shop)
        owner_session(shop, embco_session)
        try:
            interval = store.config(shop).interval_minutes
        except ShopError:
            interval = None
        return {"events": read_activity(store.folder(shop), after=after),
                "interval_minutes": interval}

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
        if not store.agent_on(shop):
            raise HubError(409, "the agent is off; turn it on first")
        try:
            units.start(shop)  # a restart: the agent checks right away
        except UnitError as error:
            raise HubError(500, "the agent did not restart; try again") from error
        last_check[shop] = now
        return {"checking": True}

    @router.post("/api/shops/{shop}/agent")
    def switch(shop: str, body: Switch,
               embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        """Turn the whole agent on or off: the shop's process, and with it Claude and every
        payment. Off stays off (a restart, "check now" or a new ERP connection do not start it)
        until the owner turns it on."""
        shop = shop_of(shop)
        owner_session(shop, embco_session)
        now = clock()
        if body.on:
            if shop not in store.connected():
                raise HubError(409, "connect the ERP first")
            store.set_agent_on(shop, True, now.isoformat())
            append_activity(store.folder(shop), [switched(now, True)])
            try:
                units.start(shop)
            except UnitError as error:
                raise HubError(500, "turned on, but the agent did not start; try again") from error
            return {"agent_on": True}
        store.set_agent_on(shop, False, now.isoformat())  # first: a running cycle stops here
        try:
            units.stop(shop)
        except UnitError as error:
            raise HubError(500, "turned off; the process did not stop but does nothing") from error
        finally:
            append_activity(store.folder(shop), [switched(now, False)])
        return {"agent_on": False}

    return router
