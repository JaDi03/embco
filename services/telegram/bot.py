"""The shop's agent on Telegram: commands that act at once, and a conversation with the agent.

Commands never go through the model:
  /stop   turn the whole agent off (its process, Claude, every payment) and pause the contract
          from the agent's wallet. Only the dashboard turns it on again, with the owner's wallet.
  /pause  pause payments only: the contract is paused and the agent only observes; it still
          checks, decides and answers.
  /status the agent's state and what it is waiting for.
Any other text is a message to the agent; its answers come back to the chat.

Only a chat linked from the dashboard is heard; any other chat is told how to link and nothing
else. A /link code is tried at most a few times per chat and hour.
"""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from eth_utils import keccak, to_checksum_address

from services.circle import CircleClient, CircleError
from services.hub.units import UnitError, Units
from services.shops import chat
from services.shops.activity import append_activity, switched
from services.shops.store import ShopError, ShopStore
from services.telegram import links
from services.telegram.api import TelegramApi, TelegramError

log = logging.getLogger("embco.telegram")

PAUSE_CALL = keccak(text="pause()")[:4]
LINK_TRIES = 5  # per chat and hour
HELP = ("Commands:\n/stop - turn the agent off now (no checks, no Claude, no payments)\n"
        "/pause - pause payments; the agent keeps watching and answering\n/status - how it is\n"
        "Anything else is a message to your agent.")
NOT_LINKED = ("This chat is not linked to a shop. In the embco dashboard, Agent card, choose "
              "Connect Telegram and send me /link followed by the code.")


def contract_pauser(circle: CircleClient, store: ShopStore) -> Callable[[str], str]:
    """Pause the shop contract with the agent's own wallet (the contract lets owner or agent
    pause; only the owner resumes). Returns Circle's transaction id."""

    def pause(shop: str) -> str:
        wallet_id = store.config(shop).agent_wallet_id
        if not wallet_id:
            raise ShopError("the shop has no agent wallet to pause the contract with")
        tx = circle.execute_contract(wallet_id, to_checksum_address(shop), PAUSE_CALL,
                                     idempotency_key=str(uuid.uuid4()), ref_id="embco-stop")
        return tx.id

    return pause


@dataclass
class Bot:
    api: TelegramApi
    store: ShopStore
    units: Units
    pause_contract: Callable[[str], str]
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    tries: dict[int, list[datetime]] = field(default_factory=dict)

    def handle(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        text = (message.get("text") or "").strip()
        if not isinstance(chat_id, int) or not text:
            return
        command = text.split()[0].split("@")[0].lower()
        if command == "/link":
            self.api.send(chat_id, self._link(chat_id, text.removeprefix(text.split()[0])))
            return
        shops = self.shops_of(chat_id)
        if not shops:
            self.api.send(chat_id, NOT_LINKED)
            return
        if command == "/stop":
            reply = "\n".join(self._stop(shop) for shop in shops)
        elif command == "/pause":
            reply = "\n".join(self._pause(shop) for shop in shops)
        elif command == "/status":
            reply = "\n\n".join(self._status(shop) for shop in shops)
        elif command in ("/start", "/help"):
            reply = HELP
        elif command.startswith("/"):
            reply = f"Unknown command.\n{HELP}"
        else:
            reply = self._to_agent(shops[0], text)
        self.api.send(chat_id, reply)

    def forward(self) -> None:
        """Send each linked chat what the agent (or the service) wrote since last time."""
        for shop in self.store.shops():
            folder = self.store.folder(shop)
            link = links.link_of(folder)
            if not link:
                continue
            fresh = [e for e in chat.read_chat(folder, after=int(link.get("forwarded", 0)))
                     if e.get("from") in (chat.AGENT, chat.SYSTEM)]
            for entry in fresh:
                cost = f"\n({entry['cost']} est.)" if entry.get("cost") else ""
                try:
                    self.api.send(int(link["chat_id"]), f"{entry['text']}{cost}")
                except TelegramError as error:
                    log.warning("could not forward to the chat of %s: %s", shop, error)
                    return
                links.set_forwarded(folder, int(entry["seq"]))

    def shops_of(self, chat_id: int) -> list[str]:
        return [s for s in self.store.shops()
                if (link := links.link_of(self.store.folder(s))) and link.get("chat_id") == chat_id]

    def _link(self, chat_id: int, code: str) -> str:
        now = self.clock()
        recent = [t for t in self.tries.get(chat_id, []) if now - t < timedelta(hours=1)]
        if len(recent) >= LINK_TRIES:
            return "Too many tries. Wait an hour and ask the dashboard for a new code."
        self.tries[chat_id] = [*recent, now]
        for shop in self.store.shops():
            if links.claim(self.store.folder(shop), code, chat_id, now):
                links.set_forwarded(self.store.folder(shop),
                                    max([e["seq"] for e in chat.read_chat(
                                        self.store.folder(shop))] or [0]))
                return f"Linked. I'm the embco agent of shop {shop}.\n{HELP}"
        return "That code is not valid or has expired. Ask the dashboard for a new one."

    def _stop(self, shop: str) -> str:
        now = self.clock()
        self.store.set_agent_on(shop, False, now.isoformat())  # first: a running cycle stops
        lines = ["Agent OFF: no checks, no Claude, no payments."]
        try:
            self.units.stop(shop)
        except UnitError:
            lines.append("Its process did not stop, but it does nothing while off.")
        append_activity(self.store.folder(shop), [{**switched(now, False),
                                                   "text": "You turned the agent off from "
                                                           "Telegram. Nothing runs until you "
                                                           "turn it on in the dashboard."}])
        lines.append(self._pause_contract(shop))
        lines.append("Turn it on again from the dashboard, with your wallet.")
        return "\n".join(lines)

    def _pause(self, shop: str) -> str:
        self.store.set_payments_paused(shop, True, self.clock().isoformat())
        return "\n".join(["Payments PAUSED. The agent keeps watching and answering, and pays "
                          "nothing.", self._pause_contract(shop),
                          "Resume from the dashboard, with your wallet."])

    def _pause_contract(self, shop: str) -> str:
        try:
            tx = self.pause_contract(shop)
        except (CircleError, ShopError) as error:
            log.warning("contract of %s not paused: %s", shop, error)
            return ("The contract could NOT be paused from here: pause it in the dashboard "
                    "with your wallet.")
        return f"Contract pause sent from the agent's wallet (Circle {tx})."

    def _status(self, shop: str) -> str:
        on = self.store.agent_on(shop)
        paused = self.store.payments_paused(shop)
        state = "OFF" if not on else "ON, payments paused" if paused else "ON"
        last = self.store.last_run(shop) or {}
        counts = last.get("counts") or {}
        session = (last.get("brain") or {}).get("session") or {}
        lines = [f"Agent: {state}"]
        if counts:
            lines.append(f"Needs you: {counts.get('ask', 0)}, on hold: {counts.get('held', 0)}")
        if session.get("summary"):
            lines.append(f"Last word from the agent: {session['summary']}")
        return "\n".join(lines)

    def _to_agent(self, shop: str, text: str) -> str:
        if not self.store.agent_on(shop):
            return "The agent is off. Turn it on in the dashboard to talk to it."
        chat.drop_message(self.store.folder(shop), text[:chat.MAX_TEXT], "owner via Telegram",
                          self.clock())
        return "Your agent is reading it..."


def run_bot(bot: Bot, *, rounds: int | None = None, sleep: Callable[[float], None] = time.sleep,
            ) -> None:
    """Listen for messages and pass on the agent's answers, until stopped."""
    offset: int | None = None
    done = 0
    while rounds is None or done < rounds:
        done += 1
        try:
            updates = bot.api.get_updates(offset)
        except TelegramError as error:
            log.warning("%s; trying again", error)
            sleep(5)
            continue
        for update in updates:
            offset = int(update["update_id"]) + 1
            try:
                bot.handle(update)
            except Exception:  # one bad message must not stop the bot
                log.exception("a Telegram update could not be handled")
        bot.forward()
