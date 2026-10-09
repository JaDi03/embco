"""Agent settings, read from environment variables (or a .env file) and checked once at start.

Secrets never appear in errors, logs or repr.
"""

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from agent.agent import DEFAULT_EFFORT as BRAIN_EFFORT
from agent.agent import DEFAULT_MODEL as BRAIN_MODEL
from agent.explain import DEFAULT_MODEL
from agent.guardrails.rules import PolicyConfig
from agent.models import Autonomy
from agent.think import DAILY_TOKENS
from services.erp.erpnext.adapter import PROTECTED_PAYMENT_FIELDS

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
EFFORTS = ("low", "medium", "high")
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
    circle_api_key: str | None = field(default=None, repr=False)
    circle_entity_secret: str | None = field(default=None, repr=False)
    agent_wallet_id: str | None = None
    pay: bool = False
    erpnext_paid_from: str | None = None
    erpnext_wallet_bank: str | None = None  # wallets in Bank Account rows of this bank
    erpnext_payment_extra: Mapping[str, str] = field(default_factory=dict)  # required by the ERP
    erpnext_draft_payments: bool = False  # testnet: leave payment entries as drafts
    approvals_file: Path | None = None
    owner_users: tuple[str, ...] = ()  # ERP accounts whose mark on an invoice answers an ASK
    shop_address: str | None = None
    arc_rpc_url: str | None = field(default=None, repr=False)
    brain: bool = True  # Claude decides what is paid and when; the rules stay the guardrails
    brain_model: str = BRAIN_MODEL
    brain_effort: str = BRAIN_EFFORT
    autonomy: Autonomy = Autonomy.ACT
    utc_offset: timedelta = timedelta(0)  # the shop's local time, for rounds and dates
    round_hour: int = 8
    brain_daily_tokens: int = DAILY_TOKENS

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
        _check_erpnext_url(get("ERPNEXT_URL"))
        pay = _flag("PAY", get("PAY"))
        settings = cls(
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
            circle_api_key=env.get("CIRCLE_API_KEY", "").strip() or None,
            circle_entity_secret=env.get("CIRCLE_ENTITY_SECRET", "").strip() or None,
            agent_wallet_id=get("AGENT_WALLET_ID") or None,
            pay=pay,
            erpnext_paid_from=get("ERPNEXT_PAID_FROM") or None,
            erpnext_wallet_bank=get("ERPNEXT_WALLET_BANK") or None,
            erpnext_payment_extra=_payment_extra(get("ERPNEXT_PAYMENT_EXTRA")),
            erpnext_draft_payments=_flag("ERPNEXT_DRAFT_PAYMENTS", get("ERPNEXT_DRAFT_PAYMENTS")),
            approvals_file=Path(get("APPROVALS_FILE")) if get("APPROVALS_FILE") else None,
            shop_address=get("SHOP_ADDRESS") or None,
            owner_users=tuple(u.strip() for u in get("OWNER_USERS").split(",") if u.strip()),
            arc_rpc_url=env.get("ARC_TESTNET_RPC_URL", "").strip() or None,
            brain=_flag("BRAIN", get("BRAIN") or "on"),
            brain_model=get("BRAIN_MODEL") or BRAIN_MODEL,
            brain_effort=_choice("BRAIN_EFFORT", get("BRAIN_EFFORT") or BRAIN_EFFORT, EFFORTS),
            autonomy=Autonomy(_choice("AUTONOMY", get("AUTONOMY") or Autonomy.ACT.value,
                                      tuple(a.value for a in Autonomy))),
            utc_offset=_utc_offset(get("UTC_OFFSET")),
            round_hour=_whole("ROUND_HOUR", get("ROUND_HOUR"), 8, 0, 23),
            brain_daily_tokens=_whole("BRAIN_DAILY_TOKENS", get("BRAIN_DAILY_TOKENS"),
                                      DAILY_TOKENS, 1, 100_000_000),
        )
        if pay:
            settings._check_payments()
        return settings

    def _check_payments(self) -> None:
        needed = {
            "CIRCLE_API_KEY": self.circle_api_key,
            "CIRCLE_ENTITY_SECRET": self.circle_entity_secret,
            f"{PREFIX}AGENT_WALLET_ID": self.agent_wallet_id,
            f"{PREFIX}SHOP_ADDRESS": self.shop_address,
            "ARC_TESTNET_RPC_URL": self.arc_rpc_url,
        }
        missing = [name for name, value in needed.items() if not value]
        if missing:
            raise SettingsError(f"{PREFIX}PAY=on needs: {', '.join(missing)}")
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", self.shop_address or ""):
            raise SettingsError(f"{PREFIX}SHOP_ADDRESS must be the shop contract's address")

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


def _check_erpnext_url(url: str) -> None:
    """Supplier wallets travel on this connection, so it must be encrypted and verified.
    Plain http is accepted only to the same machine."""
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1"):
        return
    raise SettingsError(f"{PREFIX}ERPNEXT_URL must start with https:// (http only to localhost)")


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


def _payment_extra(text: str) -> dict[str, str]:
    name = PREFIX + "ERPNEXT_PAYMENT_EXTRA"
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise SettingsError(f'{name} must be a JSON object of text values, e.g. {{"field": "01"}}')
    clash = sorted(PROTECTED_PAYMENT_FIELDS & value.keys())
    if clash:
        raise SettingsError(f"{name} cannot set {', '.join(clash)}")
    return value


def _choice(name: str, text: str, allowed: tuple[str, ...]) -> str:
    if text not in allowed:
        raise SettingsError(f"{PREFIX}{name} must be one of: {', '.join(allowed)}")
    return text


def _whole(name: str, text: str, default: int, low: int, high: int) -> int:
    if not text:
        return default
    if not text.isdigit() or not low <= int(text) <= high:
        raise SettingsError(f"{PREFIX}{name} must be a whole number from {low} to {high}")
    return int(text)


def _utc_offset(text: str) -> timedelta:
    """The shop's offset from UTC, like +01:00 (Lagos) or -05:00. No daylight saving."""
    if not text:
        return timedelta(0)
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", text)
    if not match or int(match[2]) > 14 or int(match[3]) >= 60:
        raise SettingsError(f"{PREFIX}UTC_OFFSET must look like +01:00 or -05:00")
    sign = 1 if match[1] == "+" else -1
    return sign * timedelta(hours=int(match[2]), minutes=int(match[3]))


def _flag(name: str, text: str) -> bool:
    value = text.lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise SettingsError(f"{PREFIX}{name} must be on or off")
