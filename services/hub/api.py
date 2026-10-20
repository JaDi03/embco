"""The hub's HTTP API, served under app.embco.xyz/api/ next to the dashboard.

Every shop route needs a session from the shop owner's signature. ERP keys come in once, are
checked, stored encrypted, and never sent back. Connecting starts the shop's own agent process;
disconnecting stops it and forgets the keys, keeping the agent's memory.

The dashboard signs in once for all the owner's shops. Before a new shop exists, the hub makes its
agent wallets, so the contract is created with its agent and the owner never sets one by hand.
"""

from collections.abc import Callable
from typing import Any

from eth_utils import is_address, to_checksum_address
from fastapi import Cookie, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from services.circle import CircleClient, CircleError
from services.erp.erpnext.adapter import PROTECTED_PAYMENT_FIELDS
from services.erp.erpnext.client import FrappeClient
from services.hub.auth import ALL_SHOPS, SESSION_LIFETIME, Auth, AuthError
from services.hub.chain import ShopLimits
from services.hub.errors import HubError
from services.hub.guard import UnsafeUrl, check_erp_url
from services.hub.owner_actions import owner_action_routes, waiting_answers
from services.hub.probe import probe
from services.hub.supplier_auth import SupplierAuth
from services.hub.suppliers import supplier_routes
from services.hub.units import UnitError, Units
from services.hub.wallets import agent_wallets_of, create_agent_wallet, create_agent_wallets
from services.payments.chain import ChainError
from services.shops.balances import read_balances
from services.shops.earn_moves import read_moves
from services.shops.store import ErpCredentials, ShopConfig, ShopError, ShopStore, check_shop
from services.telegram import links

COOKIE = "embco_session"
PUBLIC_URL = "https://app.embco.xyz"


class SignInRequest(BaseModel):
    address: str = Field(max_length=42)


class SignIn(BaseModel):
    nonce: str = Field(max_length=64)
    signature: str = Field(max_length=200)


class Connect(BaseModel):
    erp_url: str = Field(max_length=300)
    api_key: str = Field(min_length=1, max_length=200, repr=False)
    api_secret: str = Field(min_length=1, max_length=200, repr=False)
    company: str = Field(min_length=1, max_length=140)
    wallet_bank: str | None = Field(default=None, max_length=140)
    payment_extra: dict[str, str] = Field(default_factory=dict)

    @field_validator("payment_extra")
    @classmethod
    def _not_what_was_paid(cls, value: dict[str, str]) -> dict[str, str]:
        clash = sorted(PROTECTED_PAYMENT_FIELDS & value.keys())
        if clash:
            raise ValueError(f"cannot set {', '.join(clash)}")
        return value


def create_app(
    *,
    store: ShopStore,
    auth: Auth,
    limits_of: Callable[[str], ShopLimits],
    units: Units,
    circle: CircleClient | None = None,
    frappe_for: Callable[[str, str, str], FrappeClient] | None = None,
    check_url: Callable[[str], str] = check_erp_url,
    public_url: str = PUBLIC_URL,
    supplier_auth: SupplierAuth | None = None,
    telegram_bot: str | None = None,
    shops_of: Callable[[str], list[str]] | None = None,
) -> FastAPI:
    app = FastAPI(title="embco hub", docs_url=None, redoc_url=None, openapi_url=None)
    frappe_for = frappe_for or (lambda url, key, secret: FrappeClient(url, key, secret,
                                                                      timeout=15))

    @app.exception_handler(HubError)
    async def _hub_error(request: Request, error: HubError) -> JSONResponse:
        return JSONResponse({"error": str(error), **error.extra}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, error: RequestValidationError) -> JSONResponse:
        # Never echo the request back: it may hold the ERP secret.
        fields = sorted({".".join(str(p) for p in e["loc"][1:]) for e in error.errors()})
        return JSONResponse({"error": "invalid request", "fields": fields}, status_code=422)

    def owner_session(shop: str, token: str | None):
        try:
            return auth.session(token, shop)
        except AuthError as error:
            raise HubError(401, str(error)) from error
        except ChainError as error:
            raise HubError(502, "could not read your shops") from error

    def shop_of(raw: str) -> str:
        try:
            return check_shop(raw.lower())
        except ShopError as error:
            raise HubError(400, str(error)) from error

    @app.post("/api/shops/{shop}/sign-in-request")
    def sign_in_request(shop: str, body: SignInRequest) -> dict[str, Any]:
        if not is_address(body.address):
            raise HubError(422, "this is not a wallet address")
        return auth.challenge(shop_of(shop), to_checksum_address(body.address))

    @app.post("/api/shops/{shop}/sign-in")
    def sign_in(shop: str, body: SignIn) -> JSONResponse:
        try:
            token, session = auth.sign_in(shop_of(shop), body.nonce, body.signature)
        except AuthError as error:
            raise HubError(401, str(error)) from error
        except ChainError as error:
            raise HubError(502, "could not read the shop contract") from error
        response = JSONResponse({"owner": session.owner})
        response.set_cookie(COOKIE, token, max_age=int(SESSION_LIFETIME.total_seconds()),
                            httponly=True, secure=True, samesite="strict", path="/api")
        return response

    @app.post("/api/owners/sign-in-request")
    def owner_sign_in_request(body: SignInRequest) -> dict[str, Any]:
        if not is_address(body.address):
            raise HubError(422, "this is not a wallet address")
        return auth.challenge(ALL_SHOPS, to_checksum_address(body.address))

    @app.post("/api/owners/sign-in")
    def owner_sign_in(body: SignIn) -> JSONResponse:
        try:
            token, session = auth.sign_in(ALL_SHOPS, body.nonce, body.signature)
        except AuthError as error:
            raise HubError(401, str(error)) from error
        response = JSONResponse({"owner": session.owner})
        response.set_cookie(COOKIE, token, max_age=int(SESSION_LIFETIME.total_seconds()),
                            httponly=True, secure=True, samesite="strict", path="/api")
        return response

    @app.post("/api/owners/agent-wallets")
    def new_agent_wallets(embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        """The agent wallets for the owner's next shop: the contract is created with them."""
        owner = owner_session(ALL_SHOPS, embco_session).owner
        if circle is None or shops_of is None:
            raise HubError(503, "agent wallets are not available here")
        try:
            index = len(shops_of(owner))
        except ChainError as error:
            raise HubError(502, "could not read your shops") from error
        try:
            wallets = create_agent_wallets(circle, owner, index)
        except CircleError as error:
            raise HubError(502, "could not create the agent wallets; try again") from error
        return {"agent": wallets.agent.address, "signer": wallets.signer.address}

    @app.get("/api/shops/{shop}/agent")
    def agent_wallets(shop: str,
                      embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        """The contract's agent and the wallet that signs the owner's Unified Balance spends,
        with or without a connected ERP."""
        shop = shop_of(shop)
        owner_session(shop, embco_session)
        try:
            agent = limits_of(shop).agent
        except ChainError as error:
            raise HubError(502, "could not read the shop contract") from error
        try:
            wallets = agent_wallets_of(circle, agent) if circle else None
        except CircleError as error:
            raise HubError(502, "could not read the agent wallets; try again") from error
        if wallets is None:
            return {"agent": agent, "ours": False, "signer": None, "gasless": False}
        return {"agent": agent, "ours": True, "signer": wallets.signer.address,
                "gasless": wallets.agent.account_type == "SCA"}

    @app.post("/api/shops/{shop}/sign-out")
    def sign_out(shop: str, embco_session: str | None = Cookie(default=None)) -> JSONResponse:
        auth.sign_out(embco_session)
        response = JSONResponse({"signed_out": True})
        response.delete_cookie(COOKIE, path="/api")
        return response

    @app.get("/api/shops/{shop}")
    def status(shop: str, embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        shop = shop_of(shop)
        owner_session(shop, embco_session)
        connected = shop in store.connected()
        try:
            config = store.config(shop)
        except ShopError:
            return {"shop": shop, "connected": False,
                    "liquidity_strategy": store.liquidity_strategy(shop)}
        return {
            "shop": shop,
            "connected": connected,
            "erp_url": config.erp_url,
            "company": config.company,
            "wallet_bank": config.wallet_bank,
            "limits": {"max_per_payment": config.max_per_payment,
                       "weekly_cap": config.weekly_budget},
            "agent_wallet": config.agent_wallet_address,
            "agent_on": store.agent_on(shop),
            "payments_paused": store.payments_paused(shop),
            "draft_mode": store.draft_payments(shop),
            "liquidity_strategy": store.liquidity_strategy(shop),
            "telegram_linked": links.link_of(store.folder(shop)) is not None,
            "last_run": (last_run := store.last_run(shop)),
            "answers_waiting": waiting_answers(store, shop, last_run),
        }

    @app.get("/api/shops/{shop}/decisions")
    def decisions(shop: str, embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        shop = shop_of(shop)
        owner_session(shop, embco_session)
        return {"shop": shop, "last_run": store.last_run(shop)}

    @app.get("/api/shops/{shop}/balances")
    def balance_history(shop: str,
                        embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        shop = shop_of(shop)
        owner_session(shop, embco_session)
        folder = store.folder(shop)
        return {"shop": shop, "points": read_balances(folder), "earn_moves": read_moves(folder)}

    @app.post("/api/shops/{shop}/erp")
    def connect(shop: str, body: Connect,
                embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        shop = shop_of(shop)
        owner_session(shop, embco_session)
        try:
            base = check_url(body.erp_url)
        except UnsafeUrl as error:
            raise HubError(422, str(error)) from error
        result = probe(frappe_for(base, body.api_key, body.api_secret), body.company,
                       body.wallet_bank)
        if not result.ok:
            raise HubError(422, "the ERP keys cannot do what the agent needs",
                           probe=result.as_dict())
        try:
            limits = limits_of(shop)
        except ChainError as error:
            raise HubError(502, "could not read the shop contract") from error
        try:
            previous = store.config(shop)
        except ShopError:
            previous = None
        wallet_id = previous.agent_wallet_id if previous else None
        wallet_address = previous.agent_wallet_address if previous else None
        signer_address = previous.signer_wallet_address if previous else None
        if circle and not wallet_id:
            try:
                # a shop created with its agent: the contract already names it; an older shop
                # gets its agent wallet now
                wallets = agent_wallets_of(circle, limits.agent)
                if wallets is None:
                    wallet = create_agent_wallet(circle, shop)
            except CircleError as error:
                raise HubError(502, "could not create the agent wallet; try again") from error
            if wallets:
                wallet = wallets.agent
                if wallets.signer.id != wallet.id:
                    signer_address = wallets.signer.address
            wallet_id, wallet_address = wallet.id, wallet.address
        store.save(
            ShopConfig(shop=shop, erp_url=base, company=body.company,
                       max_per_payment=str(limits.max_per_payment),
                       weekly_budget=str(limits.weekly_cap), wallet_bank=body.wallet_bank,
                       payment_extra=body.payment_extra, agent_wallet_id=wallet_id,
                       agent_wallet_address=wallet_address,
                       signer_wallet_address=signer_address),
            ErpCredentials(api_key=body.api_key, api_secret=body.api_secret),
        )
        if store.agent_on(shop):  # turned off by the owner: a new connection does not start it
            try:
                units.start(shop)
            except UnitError as error:
                raise HubError(500, "saved, but the agent did not start; try again") from error
        return {
            "connected": True,
            "agent_on": store.agent_on(shop),
            "probe": result.as_dict(),
            "agent_wallet": wallet_address,
            "contract_agent": limits.agent,
            "set_agent_needed": bool(wallet_address)
            and wallet_address.lower() != limits.agent.lower(),
        }

    @app.delete("/api/shops/{shop}/erp")
    def disconnect(shop: str, embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        shop = shop_of(shop)
        owner_session(shop, embco_session)
        try:
            units.stop(shop)
        except UnitError as error:
            raise HubError(500, "the agent did not stop; try again") from error
        store.disconnect(shop)
        return {"connected": False}

    app.include_router(owner_action_routes(store=store, units=units, shop_of=shop_of,
                                           owner_session=owner_session,
                                           telegram_bot=telegram_bot))
    supplier_auth = supplier_auth or SupplierAuth(public_url=public_url)
    app.include_router(supplier_routes(store=store, auth=supplier_auth,
                                       shop_of=shop_of, owner_session=owner_session,
                                       frappe_for=frappe_for, public_url=public_url))
    return app
