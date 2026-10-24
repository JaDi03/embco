"""Reads and simulations on Arc through a JSON-RPC node (the Canteen node for testnet).

The node URL carries an access token, so errors never include it.
"""

from datetime import UTC, datetime
from typing import Any

import httpx
from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

WEEK_SECONDS = 7 * 24 * 3600
SHOP_LOGS_FROM = 66943142  # the ShopPayablesFactory deployment on Arc testnet: no shop is older
LOG_CHUNK_BLOCKS = 99_999  # the most blocks the Canteen node returns logs for in one call
_PAYEE_SET = "0x" + keccak(text="PayeeSet(address,bool)").hex()


class ChainError(Exception):
    pass


class Reverted(ChainError):
    """The simulated call would fail on chain; `reason` says why in contract terms."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_ERRORS = [
    "OwnableUnauthorizedAccount(address)", "NotAuthorized()", "EnforcedPause()", "ExpectedPause()",
    "PayeeNotApproved(address)", "AlreadyPaid(bytes32)", "OverPaymentLimit(uint256,uint256)",
    "OverWeeklyCap(uint256,uint256)", "InvalidLimits()", "InvalidAddress()", "InvalidPayment()",
    "SafeERC20FailedOperation(address)", "OwnershipFixed()", "ReentrancyGuardReentrantCall()",
    "OverMoveLimit(uint256,uint256)", "InvalidAmount()", "VaultNotApproved(address)",
    "InvalidVaultAsset(address,address)", "OverParkLimit(uint256,uint256)",
    "ZeroShares()", "ZeroAssets()",
]
_KNOWN = {keccak(text=sig)[:4]: sig.split("(")[0] for sig in _ERRORS}
_ERROR_STRING = keccak(text="Error(string)")[:4]


def revert_reason(data: bytes) -> str:
    """Name a revert: the shop's custom errors, or the message of an Error(string)."""
    selector, rest = data[:4], data[4:]
    if selector == _ERROR_STRING:
        try:
            (message,) = decode(["string"], rest)
        except Exception:  # noqa: BLE001
            return "Error"
        return message
    if selector in _KNOWN:
        return _KNOWN[selector]
    return f"unknown error 0x{data[:4].hex()}" if data else "reverted without a reason"


class ArcRpc:
    def __init__(self, url: str, *, client: httpx.Client | None = None, timeout: int = 20) -> None:
        self._url = url
        self._client = client or httpx.Client(timeout=timeout)

    def __repr__(self) -> str:
        return "ArcRpc(<node url hidden>)"

    def _rpc(self, method: str, params: list[Any]) -> Any:
        try:
            response = self._client.post(
                self._url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            )
        except httpx.HTTPError as exc:
            raise ChainError(f"Arc node request failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise ChainError(f"Arc node returned HTTP {response.status_code} for {method}")
        try:
            body = response.json()
        except ValueError:
            raise ChainError(f"Arc node returned invalid JSON for {method}") from None
        if "error" in body:
            error = body["error"] or {}
            data = error.get("data")
            if isinstance(data, str) and data.startswith("0x"):
                raise Reverted(revert_reason(bytes.fromhex(data[2:])))
            if "revert" in str(error.get("message", "")).lower():
                raise Reverted(str(error.get("message")))
            raise ChainError(f"Arc node error {error.get('code')} for {method}")
        return body.get("result")

    def call(self, to: str, data: bytes, *, sender: str | None = None) -> bytes:
        tx = {"to": to_checksum_address(to), "data": "0x" + data.hex()}
        if sender:
            tx["from"] = to_checksum_address(sender)
        result = self._rpc("eth_call", [tx, "latest"])
        return bytes.fromhex(result[2:]) if result else b""

    def _view(self, to: str, signature: str, types: list[str], args: list[Any]) -> bytes:
        return self.call(to, keccak(text=signature)[:4] + encode(types, args))

    def is_paid(self, shop: str, ref: bytes) -> bool:
        (paid,) = decode(["bool"], self._view(shop, "paid(bytes32)", ["bytes32"], [ref]))
        return paid

    def is_approved(self, shop: str, payee: str) -> bool:
        (approved,) = decode(["bool"], self._view(shop, "approvedPayee(address)", ["address"],
                                                  [to_checksum_address(payee)]))
        return approved

    def payee_approved_since(self, shop: str, payee: str, since: datetime,
                             first_block: int = SHOP_LOGS_FROM) -> datetime | None:
        """When the owner approved this payee in the shop contract, if that approval is its
        latest PayeeSet event and was made after `since`; otherwise None. Reads the contract's
        events newest range first and stops at ranges older than `since`."""
        topic = "0x" + "00" * 12 + to_checksum_address(payee)[2:].lower()
        try:
            end = int(self._rpc("eth_blockNumber", []), 16)
            while end >= first_block:
                start = max(first_block, end - LOG_CHUNK_BLOCKS + 1)
                logs = self._rpc("eth_getLogs", [{
                    "address": to_checksum_address(shop), "topics": [_PAYEE_SET, topic],
                    "fromBlock": hex(start), "toBlock": hex(end)}])
                if logs:
                    last = max(logs, key=lambda e: (int(e["blockNumber"], 16),
                                                    int(e["logIndex"], 16)))
                    if int(last["data"], 16) != 1:
                        return None  # the latest decision on this payee removed it
                    at = self._block_time(last["blockNumber"])
                    return at if at > since else None
                if self._block_time(hex(start)) <= since:
                    return None  # nothing set since `since`
                end = start - 1
        except (KeyError, TypeError, ValueError) as error:
            raise ChainError(f"Arc node returned unexpected logs: {type(error).__name__}") from None
        return None

    def latest_block(self) -> int:
        return int(self._rpc("eth_blockNumber", []), 16)

    def logs(self, address: str, topics: list[Any], start: int, end: int) -> list[dict[str, Any]]:
        """The contract's events in [start, end]; at most LOG_CHUNK_BLOCKS + 1 blocks per call."""
        return self._rpc("eth_getLogs", [{"address": to_checksum_address(address),
                                          "topics": topics, "fromBlock": hex(start),
                                          "toBlock": hex(end)}]) or []

    def block_time(self, block: int) -> datetime:
        return self._block_time(hex(block))

    def _block_time(self, block: str) -> datetime:
        return datetime.fromtimestamp(int(self._rpc("eth_getBlockByNumber",
                                                    [block, False])["timestamp"], 16), UTC)

    def fee_balance(self, address: str) -> int:
        """The wallet's native balance, which pays Arc network fees (USDC, 18 decimals)."""
        try:
            return int(self._rpc("eth_getBalance", [to_checksum_address(address), "latest"]), 16)
        except (TypeError, ValueError):
            raise ChainError("Arc node returned an unexpected balance") from None

    def is_paused(self, shop: str) -> bool:
        (paused,) = decode(["bool"], self._view(shop, "paused()", [], []))
        return paused

    def remaining_this_week(self, shop: str) -> int:
        (units,) = decode(["uint256"], self._view(shop, "remainingThisWeek()", [], []))
        return units

    def week_resets_at(self, shop: str) -> datetime:
        """The contract counts weeks of 7 days from its creation, not calendar weeks."""
        (started,) = decode(["uint256"], self._view(shop, "startedAt()", [], []))
        (week,) = decode(["uint256"], self._view(shop, "currentWeek()", [], []))
        return datetime.fromtimestamp(started + (week + 1) * WEEK_SECONDS, UTC)

    def owner_funds(self, shop: str) -> tuple[int, int]:
        """The owner's USDC balance and what it lets the contract take, in 6-decimal units."""
        (owner,) = decode(["address"], self._view(shop, "owner()", [], []))
        (token,) = decode(["address"], self._view(shop, "usdc()", [], []))
        (balance,) = decode(["uint256"], self._view(token, "balanceOf(address)", ["address"],
                                                    [owner]))
        (allowed,) = decode(["uint256"], self._view(token, "allowance(address,address)",
                                                    ["address", "address"],
                                                    [owner, to_checksum_address(shop)]))
        return balance, allowed

    def owner_of(self, shop: str) -> str:
        (owner,) = decode(["address"], self._view(shop, "owner()", [], []))
        return to_checksum_address(owner)

    def agent_of(self, shop: str) -> str:
        (agent,) = decode(["address"], self._view(shop, "agent()", [], []))
        return to_checksum_address(agent)
