"""Rates from Frankfurter (frankfurter.dev): free, no key, blended from central banks and other
official sources, published daily. It can also run on our own server; `base_url` points there.

One request per currency and process day: every invoice in that currency uses the same rate.
"""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx

from services.fx.rates import PAYABLE_CURRENCY, ExchangeRate, RateError

DEFAULT_URL = "https://api.frankfurter.dev"
SOURCE = "frankfurter.dev"


class Frankfurter:
    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        *,
        client: httpx.Client | None = None,
        timeout: int = 10,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)
        self._today = today
        self._seen: dict[tuple[str, date], ExchangeRate] = {}

    def usd_rate(self, currency: str) -> ExchangeRate:
        code = currency.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise RateError(f"{currency!r} is not a currency code")
        key = (code, self._today())
        if key not in self._seen:
            self._seen[key] = self._fetch(code)
        return self._seen[key]

    def _fetch(self, code: str) -> ExchangeRate:
        path = f"/v2/rate/{PAYABLE_CURRENCY}/{quote(code)}"
        try:
            response = self._client.get(f"{self._base}{path}")
        except httpx.HTTPError as exc:
            raise RateError(f"the exchange rate source did not answer ({type(exc).__name__})") \
                from exc
        if response.status_code != 200:
            raise RateError(f"the exchange rate source has no rate for {code} "
                            f"(HTTP {response.status_code})")
        try:
            body = json.loads(response.text, parse_float=Decimal)
            per_usd = Decimal(str(body["rate"]))
            on = date.fromisoformat(body["date"])
            matches = body["base"] == PAYABLE_CURRENCY and body["quote"] == code
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, InvalidOperation):
            raise RateError(f"the exchange rate source sent an unreadable rate for {code}") \
                from None
        if not matches or not per_usd.is_finite() or per_usd <= 0:
            raise RateError(f"the exchange rate source sent an unusable rate for {code}")
        return ExchangeRate(currency=code, per_usd=per_usd, on=on, source=SOURCE)
