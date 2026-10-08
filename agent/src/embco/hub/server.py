"""Start the hub: settings from its env file, the API on 127.0.0.1 behind the web server."""

import os
from collections.abc import Mapping
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from embco.circle import CircleClient
from embco.hub.api import create_app
from embco.hub.auth import Auth
from embco.hub.chain import ShopChain
from embco.hub.units import SystemdUnits
from embco.payments.chain import ArcRpc
from embco.settings import SettingsError, read_env_file
from embco.shops.store import ShopError, ShopStore, read_key

DEFAULT_FACTORY = "0x4d8efEc867e9F46c05E8359e81dEC6C7D13c95A8"  # ShopPayablesFactory, Arc testnet
DEFAULT_ROOT = "/var/lib/embco/shops"
DEFAULT_KEY = "/etc/embco/shops.key"
DEFAULT_PORT = 8090


def build_app(env: Mapping[str, str]) -> FastAPI:
    rpc_url = env.get("ARC_TESTNET_RPC_URL", "").strip()
    if not rpc_url:
        raise SettingsError("missing settings: ARC_TESTNET_RPC_URL")
    try:
        key = read_key(Path(env.get("EMBCO_SHOP_KEY_FILE") or DEFAULT_KEY))
    except ShopError as error:
        raise SettingsError(str(error)) from error
    chain = ShopChain(ArcRpc(rpc_url), env.get("EMBCO_FACTORY_ADDRESS") or DEFAULT_FACTORY)
    api_key = env.get("CIRCLE_API_KEY", "").strip()
    entity_secret = env.get("CIRCLE_ENTITY_SECRET", "").strip()
    circle = CircleClient(api_key, entity_secret) if api_key and entity_secret else None
    return create_app(
        store=ShopStore(Path(env.get("EMBCO_SHOPS_ROOT") or DEFAULT_ROOT), key),
        auth=Auth(chain.shops_of),
        limits_of=chain.limits,
        units=SystemdUnits(),
        circle=circle,
    )


def serve(env_file: Path) -> None:
    values = read_env_file(env_file) if env_file.exists() else {}
    env = {**values, **os.environ}
    port = int(env.get("EMBCO_HUB_PORT") or DEFAULT_PORT)
    uvicorn.run(build_app(env), host="127.0.0.1", port=port, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", access_log=False)
