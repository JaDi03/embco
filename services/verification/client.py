"""Sourcify API v2 and Blockscout import, with bounded, credential-safe HTTP errors."""

import time
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from services.verification.store import Store

SOURCIFY = "https://sourcify.dev/server"
USER_AGENT = "embco-verification/0.1 (+https://github.com/JaDi03/embco)"


class VerificationError(Exception):
    pass


class LogRangeError(VerificationError):
    """The node requires a smaller eth_getLogs range."""


def retry_after(value: str, now: int) -> int:
    try:
        return max(0, int(value))
    except ValueError:
        try:
            return max(0, int(parsedate_to_datetime(value).timestamp()) - now)
        except (ValueError, TypeError, OverflowError):
            return 300


class Api:
    def __init__(self, http: httpx.Client, store: Store) -> None:
        self.http, self.store = http, store
        self._history: dict[tuple[str, str], list[dict]] = {}

    def request(self, service: str, method: str, url: str, *,
                allowed: tuple[int, ...] = (), **kwargs: Any) -> tuple[int, dict]:
        now = int(time.time())
        if self.store.cooldown(service) > now:
            raise VerificationError(f"{service}: cooling down")
        try:
            response = self.http.request(method, url, headers={"User-Agent": USER_AGENT}, **kwargs)
        except httpx.HTTPError:
            self.store.cool(service, now + 60)
            raise VerificationError(f"{service}: connection failed") from None
        if response.status_code == 429 or response.status_code >= 500:
            wait = max(60, retry_after(response.headers.get("Retry-After", "300"), now))
            self.store.cool(service, now + wait)
        if response.status_code not in (200, 202, *allowed):
            raise VerificationError(f"{service}: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise VerificationError(f"{service}: invalid JSON") from None
        if not isinstance(body, dict):
            raise VerificationError(f"{service}: unexpected response")
        return response.status_code, body

    def rpc(self, chain: int, url: str, method: str, params: list) -> Any:
        _, body = self.request(f"rpc-{chain}", "POST", url,
                               allowed=(413,) if method == "eth_getLogs" else (),
                               json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if body.get("error") or "result" not in body:
            error = body.get("error") or {}
            message = str(error.get("message", "")).lower()
            if method == "eth_getLogs" and (error.get("code") == -32005
                    or any(word in message for word in ("range", "limit", "too many", "pruned"))):
                raise LogRangeError(f"rpc-{chain}: smaller log range required")
            # Never include the URL, node response or provider credentials in the error.
            raise VerificationError(f"rpc-{chain}: {method} failed")
        return body["result"]

    def bundle(self, chain: int, factory: str) -> dict:
        _, body = self.request("sourcify", "GET", f"{SOURCIFY}/v2/contract/{chain}/{factory}",
                               params={"fields": "stdJsonInput,compilation.compilerVersion,"
                                                  "deployment.blockNumber"})
        if not exact(body, chain, factory):
            raise VerificationError("sourcify: factory needs an exact match")
        return body

    def lookup(self, chain: int, address: str) -> bool:
        status, body = self.request("sourcify", "GET",
                                    f"{SOURCIFY}/v2/contract/{chain}/{address}", allowed=(404,))
        return status == 200 and exact(body, chain, address)

    def submit(self, chain: int, address: str, payload: dict) -> str:
        status, body = self.request("sourcify", "POST", f"{SOURCIFY}/v2/verify/{chain}/{address}",
                                    json=payload, allowed=(409,))
        if status == 409:
            return ""  # recheck the result; an HTTP status alone is not proof
        ticket = body.get("verificationId")
        if not isinstance(ticket, str) or not ticket:
            raise VerificationError("sourcify: missing verification ticket")
        return ticket

    def poll(self, ticket: str) -> dict:
        status, body = self.request("sourcify", "GET", f"{SOURCIFY}/v2/verify/{ticket}",
                                    allowed=(404,))
        return {"isJobCompleted": True, "error": {"customCode": "job_not_found"}} \
            if status == 404 else body

    def explorer_verified(self, explorer: str, address: str) -> bool:
        _, body = self.request(explorer, "GET", f"{explorer}/api/v2/addresses/{address}")
        return body.get("hash", "").lower() == address.lower() and body.get("is_verified") is True

    def import_sourcify(self, explorer: str, address: str) -> None:
        _, body = self.request(explorer, "POST", f"{explorer}/api",
                               params={"module": "contract", "action": "verify_via_sourcify"},
                               data={"addressHash": address})
        if str(body.get("status")) != "1":
            raise VerificationError("explorer: Sourcify import not accepted")

    def historical_logs(self, explorer: str, factory: str, topic: str,
                        start: int, end: int) -> list:
        """Read the indexed creation history, retaining pagination and caching this round."""
        key = explorer, factory.lower()
        if key not in self._history:
            logs, params = [], {}
            for _ in range(20):
                _, body = self.request(explorer, "GET",
                                       f"{explorer}/api/v2/addresses/{factory}/logs", params=params)
                items = body.get("items")
                if not isinstance(items, list):
                    raise VerificationError("explorer: invalid log page")
                logs.extend(items)
                params = body.get("next_page_params")
                if params is None:
                    self._history[key] = logs
                    break
                if not isinstance(params, dict) or not params:
                    raise VerificationError("explorer: invalid log pagination")
            else:
                raise VerificationError("explorer: history exceeds 20 pages; use an archive RPC")
        return [{"address": factory, "topics": row["topics"], "removed": False,
                 "blockNumber": hex(int(row["block_number"])),
                 "transactionHash": row["transaction_hash"]}
                for row in self._history[key]
                if start <= int(row["block_number"]) <= end
                and row["topics"][0].lower() == topic.lower()]


def exact(body: dict, chain: int, address: str) -> bool:
    return (str(body.get("chainId")) == str(chain)
            and str(body.get("address", "")).lower() == address.lower()
            and body.get("runtimeMatch") == "exact_match"
            and body.get("creationMatch") == "exact_match")
