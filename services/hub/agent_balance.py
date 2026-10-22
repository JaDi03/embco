"""The owner takes back the USDC left in the agent's wallet, for example before closing a shop.

The USDC only goes to the wallet signed in as the shop's owner: nobody, the agent included,
chooses another address. On Arc, USDC is the gas token and its ERC-20 interface moves the same
balance, so the agent's wallet sends it with an ERC-20 transfer through Circle, like its other
calls. A newer shop's agent is a smart contract account whose gas Circle pays; an older shop's
agent is an EOA that pays its own gas, so a small reserve stays behind for that transaction.
The agent must be off, so no check of its own spends the same balance at the same time.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from eth_abi import encode
from eth_utils import keccak, to_checksum_address
from fastapi import APIRouter, Cookie

from agent.guardrails.controls.base import fmt
from services.circle import CircleClient, CircleError, CircleWallet
from services.earn.client import ARC_USDC
from services.hub.errors import HubError
from services.payments import ChainError
from services.payments.encoding import USDC_DECIMALS
from services.shops.activity import agent_balance_returned, append_activity
from services.shops.store import ShopError, ShopStore

TRANSFER = keccak(text="transfer(address,uint256)")[:4]
# EOA agents only. An anchor (about 97 000 gas) cost 0.045 USDC on Arc; a transfer needs less.
GAS_RESERVE = Decimal("0.1")
UNIT = Decimal(1).scaleb(-USDC_DECIMALS)


def returnable(balance: Decimal, wallet: CircleWallet) -> Decimal:
    """What can leave the agent's wallet: all of it when Circle pays the gas."""
    left = balance if wallet.account_type == "SCA" else balance - GAS_RESERVE
    return max(Decimal(0), left).quantize(UNIT, rounding=ROUND_DOWN)


def agent_balance_routes(
    *,
    store: ShopStore,
    circle: CircleClient | None,
    balance_of: Callable[[str], Decimal] | None,
    shop_of: Callable[[str], str],
    owner_session: Callable[[str, str | None], Any],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter()

    def wallet_and_balance(shop: str) -> tuple[CircleWallet, Decimal]:
        if circle is None or balance_of is None:
            raise HubError(503, "the agent's wallet cannot be reached here")
        try:
            wallet_id = store.config(shop).agent_wallet_id
        except ShopError:
            wallet_id = None
        if not wallet_id:
            raise HubError(409, "this shop has no agent wallet")
        try:
            wallet = circle.get_wallet(wallet_id)
        except CircleError as error:
            raise HubError(502, "could not read the agent's wallet; try again") from error
        try:
            return wallet, balance_of(wallet.address)
        except ChainError as error:
            raise HubError(502, "could not read the agent's balance; try again") from error

    @router.get("/api/shops/{shop}/agent-balance")
    def balance(shop: str, embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        shop = shop_of(shop)
        owner_session(shop, embco_session)
        wallet, held = wallet_and_balance(shop)
        return {"wallet": wallet.address, "balance": fmt(held),
                "returnable": fmt(returnable(held, wallet)),
                "gasless": wallet.account_type == "SCA"}

    @router.post("/api/shops/{shop}/agent-balance/return")
    def give_back(shop: str, embco_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        shop = shop_of(shop)
        session = owner_session(shop, embco_session)
        if shop in store.connected() and store.agent_on(shop):
            raise HubError(409, "turn the agent off first; it pays its gas from this wallet")
        wallet, held = wallet_and_balance(shop)
        amount = returnable(held, wallet)
        if amount <= 0:
            raise HubError(409, "there is nothing to take back from the agent's wallet")
        owner = to_checksum_address(session.owner)
        call = TRANSFER + encode(["address", "uint256"],
                                 [owner, int(amount.scaleb(USDC_DECIMALS))])
        try:
            tx = circle.execute_contract(wallet.id, ARC_USDC, call,
                                         idempotency_key=str(uuid.uuid4()),
                                         ref_id="embco-return")
        except CircleError as error:
            raise HubError(502, "Circle did not send it; nothing moved, try again") from error
        append_activity(store.folder(shop),
                        [agent_balance_returned(clock(), amount, owner, tx.id)])
        return {"amount": fmt(amount), "to": owner, "transaction": tx.id}

    return router
