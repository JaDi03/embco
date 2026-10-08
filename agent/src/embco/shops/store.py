"""One folder per shop, so each shop's agent has its own settings, ERP keys and memory.

    <root>/<shop>/config.json      what the shop chose (no secrets)
    <root>/<shop>/erp.enc          the ERP API key and secret, encrypted with the service key
    <root>/<shop>/journal.sqlite3  the agent's memory for this shop
    <root>/<shop>/last_run.json    what the agent decided last time, for the dashboard

A shop is the address of its ShopPayables contract, in lower case. A shop's settings are built
from its folder only, never from the process environment, so no shop inherits another's.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from embco.settings import Settings

CONFIG = "config.json"
SECRETS = "erp.enc"
JOURNAL = "journal.sqlite3"
LAST_RUN = "last_run.json"
_SHOP = re.compile(r"^0x[0-9a-f]{40}$")


class ShopError(Exception):
    pass


@dataclass(frozen=True)
class ShopConfig:
    shop: str
    erp_url: str
    company: str
    max_per_payment: str
    weekly_budget: str
    wallet_bank: str | None = None
    payment_extra: dict[str, str] = field(default_factory=dict)
    interval_minutes: int = 15
    agent_wallet_id: str | None = None  # the shop's own Circle wallet, when created
    agent_wallet_address: str | None = None


@dataclass(frozen=True)
class ErpCredentials:
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)


def new_key() -> bytes:
    return Fernet.generate_key()


def read_key(path: Path) -> bytes:
    try:
        key = path.read_bytes().strip()
        Fernet(key)
    except (OSError, ValueError) as error:
        raise ShopError(f"the service key at {path} cannot be read") from error
    return key


def check_shop(shop: str) -> str:
    """A shop address is also a folder name: anything else is refused."""
    if not _SHOP.match(shop):
        raise ShopError("a shop is its contract address: 0x and 40 lower-case hex characters")
    return shop


class ShopStore:
    def __init__(self, root: Path, key: bytes) -> None:
        self.root = root
        self._fernet = Fernet(key)

    def folder(self, shop: str) -> Path:
        return self.root / check_shop(shop)

    def save(self, config: ShopConfig, credentials: ErpCredentials) -> None:
        folder = self.folder(config.shop)
        folder.mkdir(parents=True, exist_ok=True)
        secret = json.dumps({"api_key": credentials.api_key, "api_secret": credentials.api_secret})
        _write(folder / SECRETS, self._fernet.encrypt(secret.encode()))
        _write(folder / CONFIG, json.dumps(config.__dict__, indent=1).encode())

    def config(self, shop: str) -> ShopConfig:
        try:
            data = json.loads((self.folder(shop) / CONFIG).read_text(encoding="utf-8"))
            return ShopConfig(**data)
        except (OSError, ValueError, TypeError) as error:
            raise ShopError(f"shop {shop} has no usable configuration") from error

    def credentials(self, shop: str) -> ErpCredentials:
        try:
            token = (self.folder(shop) / SECRETS).read_bytes()
            data = json.loads(self._fernet.decrypt(token))
            return ErpCredentials(api_key=data["api_key"], api_secret=data["api_secret"])
        except FileNotFoundError as error:
            raise ShopError(f"shop {shop} is not connected to an ERP") from error
        except (OSError, InvalidToken, ValueError, KeyError) as error:
            raise ShopError(f"the ERP keys of shop {shop} cannot be read") from error

    def disconnect(self, shop: str) -> None:
        """Forget the ERP keys; the configuration and the memory stay."""
        (self.folder(shop) / SECRETS).unlink(missing_ok=True)

    def connected(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir()
                      if _SHOP.match(p.name) and (p / SECRETS).exists())

    def settings(self, shop: str) -> Settings:
        """Observation only: payments and AI explanations stay off in this phase."""
        config, credentials = self.config(shop), self.credentials(shop)
        env = {
            "EMBCO_ERPNEXT_URL": config.erp_url,
            "EMBCO_ERPNEXT_API_KEY": credentials.api_key,
            "EMBCO_ERPNEXT_API_SECRET": credentials.api_secret,
            "EMBCO_COMPANY": config.company,
            "EMBCO_MAX_PER_PAYMENT": config.max_per_payment,
            "EMBCO_WEEKLY_BUDGET": config.weekly_budget,
            "EMBCO_JOURNAL_PATH": str(self.folder(shop) / JOURNAL),
            "EMBCO_INTERVAL_MINUTES": str(config.interval_minutes),
            "EMBCO_ERPNEXT_WALLET_BANK": config.wallet_bank or "",
            "EMBCO_ERPNEXT_PAYMENT_EXTRA": json.dumps(config.payment_extra)
            if config.payment_extra else "",
            "EMBCO_PAY": "off",
            "EMBCO_EXPLAIN": "off",
        }
        return Settings.from_env(env)

    def write_last_run(self, shop: str, summary: dict[str, Any]) -> None:
        _write(self.folder(shop) / LAST_RUN, json.dumps(summary, indent=1).encode())

    def last_run(self, shop: str) -> dict[str, Any] | None:
        try:
            return json.loads((self.folder(shop) / LAST_RUN).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def _write(path: Path, data: bytes) -> None:
    """Replace the file at once, readable by the service user only."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
