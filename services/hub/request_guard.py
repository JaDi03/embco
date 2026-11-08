"""Every request to the hub passes here before any route.

- A body holds at most MAX_BODY bytes, counted as it arrives, so no client can fill the hub's
  memory: a Content-Length above it is refused before anything is read, and a body sent without
  one stops at the limit.
- A request that changes something under /api/ (any method but GET, HEAD and OPTIONS) must come
  from the dashboard's own origin. The ERP and other sites under the same domain count as the
  same site for the session cookie, so the browser's own word on where a request comes from
  (Sec-Fetch-Site, else Origin) is checked too. A client that sends neither is not a browser and
  holds no browser's cookie.
- Every /api/ answer carries Cache-Control: no-store: it is private to whoever signed in, so no
  browser or proxy keeps a copy.
"""

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from urllib.parse import urlsplit

from starlette.exceptions import HTTPException

from services.hub.rpc_proxy import MAX_BODY as RPC_MAX_BODY

MAX_BODY = RPC_MAX_BODY  # the largest body a page sends is a batch of node calls
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SAME_ORIGIN = frozenset({"same-origin", "none"})  # "none": typed in the address bar
TOO_LARGE = "the request is too large"
OTHER_ORIGIN = "this request did not come from the embco dashboard"

Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
App = Callable[[MutableMapping[str, Any], Receive, Send], Awaitable[None]]


class BodyTooLarge(HTTPException):
    """Raised while a route reads a body past the limit; the app answers it with 413."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail=TOO_LARGE)


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


class RequestGuard:
    def __init__(self, app: App, *, public_url: str, max_body: int = MAX_BODY) -> None:
        self.app = app
        self.origin = origin_of(public_url)
        self.max_body = max_body

    async def __call__(self, scope: MutableMapping[str, Any], receive: Receive,
                       send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers") or []}
        api = scope["path"].startswith("/api/")
        length = headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > self.max_body):
            await _refuse(send, 413, TOO_LARGE)
            return
        if api and scope["method"] not in SAFE_METHODS and not self._same_origin(headers):
            await _refuse(send, 403, OTHER_ORIGIN)
            return
        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > self.max_body:
                    raise BodyTooLarge
            return message

        async def private(message: Message) -> None:
            if api and message["type"] == "http.response.start":
                message["headers"] = [
                    *((k, v) for k, v in message.get("headers") or []
                      if k.lower() != b"cache-control"),
                    (b"cache-control", b"no-store"),
                ]
            await send(message)

        await self.app(scope, counted, private)

    def _same_origin(self, headers: dict[str, str]) -> bool:
        site = headers.get("sec-fetch-site")
        if site is not None:
            return site.lower() in SAME_ORIGIN
        origin = headers.get("origin")
        return origin is None or origin.lower() == self.origin


async def _refuse(send: Send, status: int, error: str) -> None:
    body = json.dumps({"error": error}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})
