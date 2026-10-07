"""Authenticated access to the Frappe REST API: reads, plus the few writes the agent makes.

JSON is parsed with parse_float=Decimal so a value such as 249.995 is never rounded by a
binary float on the way in. Error messages never include credentials or response bodies.
"""

import json
import re
import secrets
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx

from embco.ledger.base import LedgerError


class FrappeClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        client: httpx.Client | None = None,
        timeout: int = 20,
    ) -> None:
        self._client = client or httpx.Client(timeout=timeout)
        self._base = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"token {api_key}:{api_secret}",
            "Accept": "application/json",
        }

    def _send(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(
                method, f"{self._base}{path}",
                headers={**self._headers, **kwargs.pop("headers", {})}, **kwargs
            )
        except httpx.HTTPError as exc:
            raise LedgerError(f"ERPNext request failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise LedgerError(f"ERPNext returned HTTP {response.status_code} for {path}"
                              f"{_exc_type(response)}")
        try:
            return json.loads(response.text, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"ERPNext returned invalid JSON for {path}") from exc

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return self._send("GET", path, params=params).get("data")

    def call_method(self, method: str, form: dict[str, str]) -> Any:
        """A whitelisted server method, such as the one that drafts a payment entry."""
        return self._send("POST", f"/api/method/{method}", data=form).get("message")

    def insert_doc(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Create a document; with docstatus 1 it is submitted in the same step."""
        body = _dumps_exact(doc)
        data = self._send("POST", f"/api/resource/{quote(doc['doctype'])}", content=body,
                          headers={"Content-Type": "application/json"}).get("data")
        if not isinstance(data, dict):
            raise LedgerError(f"ERPNext returned no document after creating a {doc['doctype']}")
        return data

    def get_doc(self, doctype: str, name: str) -> dict[str, Any]:
        data = self._get(f"/api/resource/{quote(doctype)}/{quote(name, safe='')}")
        if not isinstance(data, dict):
            raise LedgerError(f"ERPNext returned no document for {doctype} {name}")
        return data

    def list_rows(
        self,
        doctype: str,
        fields: list[str],
        filters: list[list[Any]],
        order_by: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        params = {
            "fields": json.dumps(fields),
            "filters": json.dumps(filters),
            "order_by": order_by,
            "limit_page_length": str(limit),
        }
        return self._get(f"/api/resource/{quote(doctype)}", params) or []

    def list_names(
        self, doctype: str, filters: list[list[Any]], order_by: str, limit: int = 500
    ) -> list[str]:
        rows = self.list_rows(doctype, ["name"], filters, order_by, limit)
        return [row["name"] for row in rows]


def _dumps_exact(doc: dict[str, Any]) -> str:
    """JSON with every Decimal written as a number with its exact digits, never via a float.

    ERPNext does arithmetic on these fields, so they must be numbers, not strings. Each call
    uses a random marker, so no text in the document can be mistaken for an amount.
    """
    marker = f"decimal-{secrets.token_hex(8)}:"

    def mark(value: Any) -> str:
        if isinstance(value, Decimal) and value.is_finite():
            return marker + format(value, "f")
        raise TypeError(f"cannot send {type(value).__name__} to ERPNext")

    pattern = re.compile(f'"{re.escape(marker)}(-?[0-9]+(?:[.][0-9]+)?)"')
    return pattern.sub(r"\1", json.dumps(doc, default=mark))


def _exc_type(response: httpx.Response) -> str:
    """Frappe's exception class (e.g. ValidationError), never its message, which may quote data."""
    try:
        exc_type = response.json().get("exc_type")
    except (ValueError, AttributeError):
        return ""
    return f" ({exc_type})" if isinstance(exc_type, str) and exc_type.isidentifier() else ""
