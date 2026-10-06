"""Agent settings, read from environment variables (or a .env file) and checked once at start.

Secrets never appear in errors, logs or repr.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from embco.decision import PolicyConfig
from embco.llm import DEFAULT_MODEL

PREFIX = "EMBCO_"
REQUIRED = (
    "ERPNEXT_URL",
    "ERPNEXT_API_KEY",
    "ERPNEXT_API_SECRET",
    "COMPANY",
    "MAX_PER_PAYMENT",
    "WEEKLY_BUDGET",
)
DEFAULT_JOURNAL = "data/journal.sqlite3"
DEFAULT_INTERVAL_MINUTES = 15
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"", "0", "false", "no", "off"}


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    erpnext_url: str
    erpnext_api_key: str = field(repr=False)
    erpnext_api_secret: str = field(repr=False)
    company: str
    policy: PolicyConfig
    journal_path: Path = Path(DEFAULT_JOURNAL)
    interval: timedelta = timedelta(minutes=DEFAULT_INTERVAL_MINUTES)
    explain: bool = False
    llm_model: str = DEFAULT_MODEL
    anthropic_api_key: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        def get(name: str) -> str:
            return env.get(PREFIX + name, "").strip()

        missing = [PREFIX + name for name in REQUIRED if not get(name)]
        if missing:
            raise SettingsError(f"missing settings: {', '.join(missing)}")
        policy = PolicyConfig(
            max_per_payment=_amount("MAX_PER_PAYMENT", get("MAX_PER_PAYMENT")),
            weekly_budget=_amount("WEEKLY_BUDGET", get("WEEKLY_BUDGET")),
            max_price_increase=_amount(
                "MAX_PRICE_INCREASE", get("MAX_PRICE_INCREASE") or "0.15"
            ),
        )
        return cls(
            erpnext_url=get("ERPNEXT_URL"),
            erpnext_api_key=get("ERPNEXT_API_KEY"),
            erpnext_api_secret=get("ERPNEXT_API_SECRET"),
            company=get("COMPANY"),
            policy=policy,
            journal_path=Path(get("JOURNAL_PATH") or DEFAULT_JOURNAL),
            interval=timedelta(minutes=_minutes(get("INTERVAL_MINUTES"))),
            explain=_flag("EXPLAIN", get("EXPLAIN")),
            llm_model=get("LLM_MODEL") or DEFAULT_MODEL,
            anthropic_api_key=env.get("ANTHROPIC_API_KEY", "").strip() or None,
        )

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Settings":
        """Process environment first; the .env file only fills what is not set."""
        values = read_env_file(env_file) if env_file and env_file.exists() else {}
        return cls.from_env({**values, **os.environ})


def read_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _amount(name: str, text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise SettingsError(f"{PREFIX}{name} must be a number, got {text!r}") from None
    if not value.is_finite() or value <= 0:
        raise SettingsError(f"{PREFIX}{name} must be greater than zero")
    return value


def _minutes(text: str) -> int:
    if not text:
        return DEFAULT_INTERVAL_MINUTES
    if not text.isdigit() or int(text) < 1:
        raise SettingsError(f"{PREFIX}INTERVAL_MINUTES must be a whole number of minutes")
    return int(text)


def _flag(name: str, text: str) -> bool:
    value = text.lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise SettingsError(f"{PREFIX}{name} must be on or off")
