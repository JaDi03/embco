"""Start the Telegram bot: `embco telegram`, a process of its own, so the emergency /stop works
even when the hub or a shop's agent does not."""

import logging
from pathlib import Path

from services.circle import CircleClient
from services.hub.units import SystemdUnits
from services.settings import SettingsError, read_env_file
from services.shops.store import ShopError, ShopStore, read_key
from services.telegram.api import TelegramApi
from services.telegram.bot import Bot, contract_pauser, run_bot

DEFAULT_ROOT = "/var/lib/embco/shops"
DEFAULT_KEY = "/etc/embco/shops.key"


def serve_bot(env_file: Path) -> None:
    env = read_env_file(env_file) if env_file.exists() else {}
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    api_key = env.get("CIRCLE_API_KEY", "").strip()
    entity_secret = env.get("CIRCLE_ENTITY_SECRET", "").strip()
    if not token:
        raise SettingsError("missing settings: TELEGRAM_BOT_TOKEN")
    if not (api_key and entity_secret):
        raise SettingsError("missing settings: CIRCLE_API_KEY, CIRCLE_ENTITY_SECRET")
    try:
        key = read_key(Path(env.get("EMBCO_SHOP_KEY_FILE") or DEFAULT_KEY))
    except ShopError as error:
        raise SettingsError(str(error)) from error
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request log shows the token
    store = ShopStore(Path(env.get("EMBCO_SHOPS_ROOT") or DEFAULT_ROOT), key)
    bot = Bot(api=TelegramApi(token), store=store, units=SystemdUnits(),
              pause_contract=contract_pauser(CircleClient(api_key, entity_secret), store))
    logging.getLogger("embco.telegram").info("Telegram bot listening")
    run_bot(bot)
