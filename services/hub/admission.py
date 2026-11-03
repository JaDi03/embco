"""Who may connect a new shop: a cap on connected shops, and a word to the operator about each one.

Anyone with a wallet can create a shop contract on testnet for free, and connecting its ERP starts
a process on this server and the AI model on the platform's account. Each shop's model already has
a daily budget (agent/think.py); this caps how many shops run, and tells the operator on Telegram
when a new one connects, so a stranger's shop is seen at once. A shop already known (its
configuration stays after a disconnect) always connects again, even at the cap.
"""

import logging
from collections.abc import Callable, Mapping

from services.telegram.api import TelegramApi, TelegramError

log = logging.getLogger("embco")

MAX_CONNECTED_SHOPS = 20

Notify = Callable[[str], None]


def max_shops(env: Mapping[str, str]) -> int:
    raw = (env.get("EMBCO_MAX_SHOPS") or "").strip()
    return int(raw) if raw else MAX_CONNECTED_SHOPS


def operator_notify(env: Mapping[str, str]) -> Notify | None:
    """A message to the operator's Telegram chat, or None when the bot or the chat is not set."""
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("EMBCO_OPERATOR_TELEGRAM_CHAT") or "").strip()
    if not token or not chat:
        return None
    api, chat_id = TelegramApi(token), int(chat)

    def notify(text: str) -> None:
        api.send(chat_id, text)
    return notify


def new_shop_text(*, shop: str, owner: str, network: str, erp_url: str, company: str,
                  connected: int, limit: int) -> str:
    return (f"New shop connected on {network}: {shop}\n"
            f"Owner: {owner}\nERP: {erp_url} ({company})\n"
            f"Connected shops: {connected} of {limit}")


def tell(notify: Notify | None, text: str) -> None:
    """A failed message never undoes the connection: it is logged and the shop runs."""
    if notify is None:
        return
    try:
        notify(text)
    except TelegramError as error:
        log.warning("the operator was not told about a new shop: %s", error)
