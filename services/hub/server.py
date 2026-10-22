"""Start the hub: settings from its env file, the API on 127.0.0.1 behind the web server."""

import os
from collections.abc import Mapping
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from services.circle import CircleClient
from services.hub.api import PUBLIC_URL, create_app
from services.hub.auth import Auth
from services.hub.chain import DEFAULT_FACTORY, ShopChain
from services.hub.units import SystemdUnits, start_connected
from services.payments.chain import ArcRpc
from services.settings import SettingsError, read_env_file
from services.shops.store import ShopError, ShopStore, read_key

DEFAULT_ROOT = "/var/lib/embco/shops"
DEFAULT_KEY = "/etc/embco/shops.key"
DEFAULT_PORT = 8090


def _store(env: Mapping[str, str]) -> ShopStore:
    try:
        key = read_key(Path(env.get("EMBCO_SHOP_KEY_FILE") or DEFAULT_KEY))
    except ShopError as error:
        raise SettingsError(str(error)) from error
    return ShopStore(Path(env.get("EMBCO_SHOPS_ROOT") or DEFAULT_ROOT), key)


def build_app(env: Mapping[str, str]) -> FastAPI:
    rpc_url = env.get("ARC_TESTNET_RPC_URL", "").strip()
    if not rpc_url:
        raise SettingsError("missing settings: ARC_TESTNET_RPC_URL")
    store = _store(env)
    chain = ShopChain(ArcRpc(rpc_url), env.get("EMBCO_FACTORY_ADDRESS") or DEFAULT_FACTORY)
    api_key = env.get("CIRCLE_API_KEY", "").strip()
    entity_secret = env.get("CIRCLE_ENTITY_SECRET", "").strip()
    circle = CircleClient(api_key, entity_secret) if api_key and entity_secret else None
    public_url = (env.get("EMBCO_PUBLIC_URL") or PUBLIC_URL).rstrip("/")
    return create_app(
        store=store,
        auth=Auth(chain.shops_of, public_url=public_url,
                  # inside the shops folder: the only place systemd lets the hub write
                  sessions_file=store.root / "owner_sessions.json"),
        limits_of=chain.limits,
        units=SystemdUnits(),
        circle=circle,
        public_url=public_url,
        telegram_bot=env.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@") or None,
        shops_of=chain.shops_of,
        balance_of=chain.usdc_balance,
    )


def serve(env_file: Path) -> None:
    values = read_env_file(env_file) if env_file.exists() else {}
    env = {**values, **os.environ}
    port = int(env.get("EMBCO_HUB_PORT") or DEFAULT_PORT)
    app = build_app(env)
    start_connected(_store(env), SystemdUnits())  # after a reboot, the shops' agents come back
    uvicorn.run(app, host="127.0.0.1", port=port, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", access_log=False)
