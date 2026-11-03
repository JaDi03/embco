"""The site's /rpc: the dashboard's door to the Arc node, and the node the owner's wallet uses for
Arc once the dashboard added the network. The node URL carries an access token, so the page and
the wallet never see it.

Anyone can reach /rpc, so it only passes what a page and a wallet send: JSON-RPC calls in the
eth_, net_ and web3_ namespaces (reads, gas, fees, receipts, signed transactions), never a node's
debug, trace, txpool, admin or personal methods. A request holds one call or a batch of at most
MAX_BATCH (the node answers 413 to more), its body at most MAX_BODY bytes, and each client
address gets at most MAX_CALLS_PER_MINUTE calls, enough for a dashboard reading a shop's history
from the node when the explorer fails.
"""

import json
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

ALLOWED_PREFIXES = ("eth_", "net_", "web3_")
MAX_BATCH = 10
MAX_BODY = 64 * 1024
MAX_CALLS_PER_MINUTE = 600
MAX_CLIENTS = 10_000  # addresses remembered at once; the oldest is forgotten first
TIMEOUT = 30


def _error(call_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": call_id, "error": {"code": code, "message": message}}


class CallBudget:
    """Calls per client address in the last minute."""

    def __init__(self, per_minute: int = MAX_CALLS_PER_MINUTE,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._per_minute = per_minute
        self._clock = clock
        self._seen: OrderedDict[str, deque[float]] = OrderedDict()

    def take(self, client: str, calls: int) -> bool:
        now = self._clock()
        times = self._seen.pop(client, deque())
        while times and now - times[0] >= 60:
            times.popleft()
        allowed = len(times) + calls <= self._per_minute
        if allowed:
            times.extend([now] * calls)
        self._seen[client] = times
        while len(self._seen) > MAX_CLIENTS:
            self._seen.popitem(last=False)
        return allowed


def rpc_routes(node_url: str, *, client: httpx.AsyncClient | None = None,
               budget: CallBudget | None = None, path: str = "/rpc") -> APIRouter:
    """One network's door: /rpc for Arc testnet, /rpc/mainnet for Arc mainnet."""
    router = APIRouter()
    http = client or httpx.AsyncClient(timeout=TIMEOUT)
    budget = budget or CallBudget()

    @router.post(path)
    async def rpc(request: Request) -> Response:
        body = await request.body()
        if len(body) > MAX_BODY:
            return JSONResponse(_error(None, -32600, "request too large"), status_code=413)
        try:
            payload = json.loads(body)
        except ValueError:
            return JSONResponse(_error(None, -32700, "parse error"), status_code=400)
        calls = payload if isinstance(payload, list) else [payload]
        if not calls or len(calls) > MAX_BATCH or not all(isinstance(c, dict) for c in calls):
            return JSONResponse(_error(None, -32600, f"send 1 to {MAX_BATCH} calls"),
                                status_code=413 if len(calls) > MAX_BATCH else 400)
        refused = [c for c in calls
                   if not str(c.get("method") or "").startswith(ALLOWED_PREFIXES)]
        if refused:
            answers = [_error(c.get("id"), -32601, "method not available here") for c in refused]
            return JSONResponse(answers if isinstance(payload, list) else answers[0])
        if not budget.take(request.client.host if request.client else "", len(calls)):
            return JSONResponse(_error(None, -32005, "too many requests; try again in a minute"),
                                status_code=429)
        try:
            answer = await http.post(node_url, content=body,
                                     headers={"Content-Type": "application/json"})
        except httpx.HTTPError as error:
            return JSONResponse(_error(None, -32603, f"the node did not answer: "
                                                     f"{type(error).__name__}"), status_code=502)
        return Response(answer.content, status_code=answer.status_code,
                        media_type="application/json")

    return router
