"""Reads and simulations on Arc through a JSON-RPC node (the Canteen node for testnet).

The node URL carries an access token, so errors never include it.
"""

from typing import Any

import httpx
from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address


class ChainError(Exception):
    pass


class Reverted(ChainError):
    """The simulated call would fail on chain; `reason` says why in contract terms."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_ERRORS = [
    "NotOwner()", "NotAuthorized()", "IsPaused()", "PayeeNotApproved(address)",
    "AlreadyPaid(bytes32)", "OverPaymentLimit(uint256,uint256)", "OverWeeklyCap(uint256,uint256)",
    "InvalidLimits()", "InvalidAddress()", "InvalidPayment()", "TransferFailed()",
]
_KNOWN = {keccak(text=sig)[:4]: sig.split("(")[0] for sig in _ERRORS}
_MEMO_FAILED = keccak(text="MemoFailed(bytes)")[:4]
_ERROR_STRING = keccak(text="Error(string)")[:4]


def revert_reason(data: bytes) -> str:
    """Name a revert: unwraps Memo's MemoFailed(bytes) and reads the shop's custom errors."""
    selector, rest = data[:4], data[4:]
    if selector == _MEMO_FAILED:
        try:
            (inner,) = decode(["bytes"], rest)
        except Exception:  # noqa: BLE001  (undecodable revert data is still a revert)
            return "MemoFailed"
        return revert_reason(inner)
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

    def remaining_this_week(self, shop: str) -> int:
        (units,) = decode(["uint256"], self._view(shop, "remainingThisWeek()", [], []))
        return units

    def agent_of(self, shop: str) -> str:
        (agent,) = decode(["address"], self._view(shop, "agent()", [], []))
        return to_checksum_address(agent)
