"""Start the Telegram bot: `embco telegram`, a process of its own, so the emergency /stop works
even when the hub or a shop's agent does not."""

import logging
from pathlib import Path

from services.circle import CircleClient
from services.hub.units import SystemdUnits
from services.network import NETWORKS, TESTNET
from services.settings import SettingsError, read_env_file
from services.shops.store import ShopError, ShopStore, read_key
from services.telegram.api import TelegramApi
from services.telegram.bot import Bot, contract_pauser, run_bot

DEFAULT_ROOT = "/var/lib/embco/shops"
DEFAULT_KEY = "/etc/embco/shops.key"


def serve_bot(env_file: Path) -> None:
    env = read_env_file(env_file) if env_file.exists() else {}
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SettingsError("missing settings: TELEGRAM_BOT_TOKEN")
    # each network's Circle account, so /stop pauses a shop's contract on its own network
    circles = {}
    for net in NETWORKS.values():
        api_key = env.get(net.circle_key_env, "").strip()
        entity_secret = env.get(net.circle_secret_env, "").strip()
        if api_key and entity_secret:
            circles[net.name] = CircleClient(api_key, entity_secret)
    if TESTNET.name not in circles:
        raise SettingsError(f"missing settings: {TESTNET.circle_key_env}, "
                            f"{TESTNET.circle_secret_env}")
    try:
        key = read_key(Path(env.get("EMBCO_SHOP_KEY_FILE") or DEFAULT_KEY))
    except ShopError as error:
        raise SettingsError(str(error)) from error
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request log shows the token
    store = ShopStore(Path(env.get("EMBCO_SHOPS_ROOT") or DEFAULT_ROOT), key)
    bot = Bot(api=TelegramApi(token), store=store, units=SystemdUnits(),
              pause_contract=contract_pauser(circles, store))
    logging.getLogger("embco.telegram").info("Telegram bot listening")
    run_bot(bot)
