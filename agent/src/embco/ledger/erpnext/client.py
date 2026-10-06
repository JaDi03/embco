"""Authenticated read access to the Frappe REST API.

JSON is parsed with parse_float=Decimal so a value such as 249.995 is never rounded by a
binary float on the way in. Error messages never include credentials or response bodies.
"""

import json
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

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        try:
            response = self._client.get(
                f"{self._base}{path}", headers=self._headers, params=params
            )
        except httpx.HTTPError as exc:
            raise LedgerError(f"ERPNext request failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise LedgerError(f"ERPNext returned HTTP {response.status_code} for {path}")
        try:
            payload = json.loads(response.text, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"ERPNext returned invalid JSON for {path}") from exc
        return payload.get("data")

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
