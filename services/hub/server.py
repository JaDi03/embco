"""Start the hub: settings from its env file, the API on 127.0.0.1 behind the web server."""

import os
from collections.abc import Callable, Mapping
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from services.circle import CircleClient
from services.hub.admission import max_shops, operator_notify
from services.hub.api import PUBLIC_URL, create_app
from services.hub.auth import Auth
from services.hub.chain import ShopChain, ShopChains
from services.hub.units import SystemdUnits, start_connected
from services.network import TESTNET, available
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


def _saved_network(store: ShopStore) -> Callable[[str], str | None]:
    def saved(shop: str) -> str | None:
        try:
            return store.config(shop).network
        except ShopError:
            return None  # not connected yet: the contract tells
    return saved


def build_app(env: Mapping[str, str]) -> FastAPI:
    """Testnet is required; mainnet is served once its node is set (ARC_MAINNET_RPC_URL)."""
    if not env.get(TESTNET.rpc_env, "").strip():
        raise SettingsError(f"missing settings: {TESTNET.rpc_env}")
    store = _store(env)
    chains, circles, rpc_urls = {}, {}, {}
    for net in available(env):
        rpc_url = env[net.rpc_env].strip()
        rpc = ArcRpc.of(rpc_url, net)
        factory = (env.get("EMBCO_FACTORY_ADDRESS") or net.factory) if net is TESTNET \
            else net.factory
        chains[net.name] = (net, ShopChain(rpc, factory), rpc)
        api_key = env.get(net.circle_key_env, "").strip()
        entity_secret = env.get(net.circle_secret_env, "").strip()
        if api_key and entity_secret:
            circles[net.name] = CircleClient(api_key, entity_secret)
        rpc_urls[net.name] = rpc_url  # the site's /rpc door, for the dashboard and the wallet
    shops = ShopChains(chains, _saved_network(store))
    public_url = (env.get("EMBCO_PUBLIC_URL") or PUBLIC_URL).rstrip("/")
    return create_app(
        store=store,
        auth=Auth(shops.shops_of, public_url=public_url,
                  # inside the shops folder: the only place systemd lets the hub write
                  sessions_file=store.root / "owner_sessions.json"),
        limits_of=shops.limits,
        units=SystemdUnits(),
        public_url=public_url,
        telegram_bot=env.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@") or None,
        shops_of=shops.shops_of,
        network_of=shops.network_of,
        circles=circles,
        balances={name: chain.usdc_balance for name, (_net, chain, _rpc) in chains.items()},
        rpc_urls=rpc_urls,
        max_shops=max_shops(env),
        notify_operator=operator_notify(env),
    )


def serve(env_file: Path) -> None:
    values = read_env_file(env_file) if env_file.exists() else {}
    env = {**values, **os.environ}
    port = int(env.get("EMBCO_HUB_PORT") or DEFAULT_PORT)
    app = build_app(env)
    start_connected(_store(env), SystemdUnits())  # after a reboot, the shops' agents come back
    uvicorn.run(app, host="127.0.0.1", port=port, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", access_log=False)
