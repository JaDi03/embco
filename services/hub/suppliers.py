"""Supplier pages: a supplier signs in with the wallet it is paid to and sees, for every shop that
pays it through embco, its invoices, its payments and the wallet signature the agent waits for.

Nobody receives a link: the owner's email (sent from the shop's ERP) tells the supplier which
address to type. A signature is checked here so the supplier knows at once whether it is right,
then left in the shop's inbox: the shop's agent records it on its next cycle.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import is_address, to_checksum_address
from fastapi import APIRouter, Cookie
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.erp.base import LedgerError
from services.erp.erpnext.client import FrappeClient
from services.erp.erpnext.mail import email_supplier_notice
from services.hub.auth import SESSION_LIFETIME, AuthError
from services.hub.errors import HubError
from services.hub.supplier_auth import SupplierAuth, SupplierSession
from services.shops.inbox import drop_signature, signature_waiting
from services.shops.store import ShopError, ShopStore

COOKIE = "embco_supplier"
PAGE_PATH = "/supplier"


class SignInRequest(BaseModel):
    wallet: str = Field(max_length=42)


class SignIn(BaseModel):
    nonce: str = Field(max_length=64)
    signature: str = Field(max_length=200)


class WalletSignature(BaseModel):
    shop: str = Field(max_length=42)
    supplier: str = Field(min_length=1, max_length=140)
    signature: str = Field(min_length=2, max_length=200)


def supplier_routes(
    *,
    store: ShopStore,
    auth: SupplierAuth,
    shop_of: Callable[[str], str],
    owner_session: Callable[[str, str | None], Any],
    frappe_for: Callable[[str, str, str], FrappeClient],
    public_url: str,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter()
    parts = urlsplit(public_url)
    site = f"{parts.netloc}{PAGE_PATH}"  # what the supplier types, without https://

    def entries(shop: str) -> dict[str, Any]:
        return ((store.supplier_view(shop) or {}).get("suppliers")) or {}

    # ---- the owner, signed in

    @router.get("/api/shops/{shop}/suppliers")
    def suppliers(shop: str,
                  embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        shop = shop_of(shop)
        owner_session(shop, embco_session)
        view = store.supplier_view(shop) or {}
        rows = [{"supplier": name, "wallet": entry.get("wallet"),
                 "open_invoices": len(entry.get("invoices") or []),
                 "payments": len(entry.get("payments") or []),
                 "signature_needed": entry.get("challenge") is not None}
                for name, entry in sorted(entries(shop).items())]
        return {"shop": shop, "at": view.get("at"), "site": site, "suppliers": rows}

    @router.post("/api/shops/{shop}/suppliers/{supplier}/email")
    def email_notice(shop: str, supplier: str,
                     embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        shop = shop_of(shop)
        owner_session(shop, embco_session)
        entry = entries(shop).get(supplier)
        if entry is None:
            raise HubError(404, "the agent has not seen this supplier yet")
        try:
            config, keys = store.config(shop), store.credentials(shop)
        except ShopError as error:
            raise HubError(409, "connect the ERP first") from error
        try:
            email_supplier_notice(frappe_for(config.erp_url, keys.api_key, keys.api_secret),
                                  supplier, config.company, site, entry.get("wallet"),
                                  entry.get("challenge") is not None)
        except LedgerError as error:
            raise HubError(502, str(error)) from error
        return {"supplier": supplier, "sent": True}

    # ---- the supplier, with its wallet

    def supplier_session(token: str | None) -> SupplierSession:
        try:
            return auth.session(token)
        except AuthError as error:
            raise HubError(401, str(error)) from error

    def mine(wallet: str) -> list[dict[str, Any]]:
        """Every shop entry whose wallet on file is this wallet."""
        found = []
        for shop in store.shops():
            view = store.supplier_view(shop) or {}
            for name, entry in sorted(((view.get("suppliers")) or {}).items()):
                if _wallet_of(entry).lower() == wallet.lower():
                    found.append({"shop": shop, "company": view.get("company"),
                                  "network": view.get("network"), "at": view.get("at"),
                                  "supplier": name, **entry,
                                  "signature_received": signature_waiting(store.folder(shop),
                                                                          name)})
        return found

    @router.post("/api/supplier/sign-in-request")
    def sign_in_request(body: SignInRequest) -> dict[str, Any]:
        if not is_address(body.wallet):
            raise HubError(422, "this is not a wallet address")
        try:
            return auth.challenge(to_checksum_address(body.wallet))
        except AuthError as error:
            raise HubError(429, str(error)) from error

    @router.post("/api/supplier/sign-in")
    def sign_in(body: SignIn) -> JSONResponse:
        try:
            token, session = auth.sign_in(body.nonce, body.signature)
        except AuthError as error:
            raise HubError(401, str(error)) from error
        response = JSONResponse({"wallet": session.wallet})
        response.set_cookie(COOKIE, token, max_age=int(SESSION_LIFETIME.total_seconds()),
                            httponly=True, secure=True, samesite="strict", path="/api/supplier")
        return response

    @router.post("/api/supplier/sign-out")
    def sign_out(embco_supplier: str | None = Cookie(default=None)) -> JSONResponse:
        auth.sign_out(embco_supplier)
        response = JSONResponse({"signed_out": True})
        response.delete_cookie(COOKIE, path="/api/supplier")
        return response

    @router.get("/api/supplier/me")
    def me(embco_supplier: str | None = Cookie(default=None)) -> dict[str, Any]:
        session = supplier_session(embco_supplier)
        return {"wallet": session.wallet, "shops": mine(session.wallet)}

    @router.post("/api/supplier/signature")
    def sign_wallet(body: WalletSignature,
                    embco_supplier: str | None = Cookie(default=None)) -> dict[str, Any]:
        session = supplier_session(embco_supplier)
        shop = shop_of(body.shop)
        entry = entries(shop).get(body.supplier)
        if entry is None or _wallet_of(entry).lower() != session.wallet.lower():
            raise HubError(404, "nothing to sign for this wallet at this shop")
        challenge = entry.get("challenge")
        if not challenge:
            raise HubError(409, "there is nothing to sign right now")
        now = clock()
        if datetime.fromisoformat(challenge["expires_at"]) <= now:
            raise HubError(409, "this request expired; the shop's agent will issue a new one")
        signer = _signer(challenge["typed_data"], body.signature)
        if signer is None or signer.lower() != str(challenge["wallet"]).lower():
            raise HubError(422, "the signature was not made by the wallet the shop has on file",
                           wallet=challenge["wallet"])
        drop_signature(store.folder(shop), body.supplier, body.signature, now)
        return {"received": True, "wallet": challenge["wallet"]}

    return router


def _wallet_of(entry: dict[str, Any]) -> str:
    """The wallet on file; while a challenge is open it is the wallet being confirmed."""
    challenge = entry.get("challenge") or {}
    return str(challenge.get("wallet") or entry.get("wallet") or "")


def _signer(typed: dict[str, Any], signature: str) -> str | None:
    try:
        return Account.recover_message(encode_typed_data(full_message=typed),
                                       signature=signature)
    except Exception:  # noqa: BLE001 (any malformed signature means "not signed")
        return None
