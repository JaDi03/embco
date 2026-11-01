"""Durable discovery cursors, frozen compilations, jobs and provider cooldowns."""

import json
import sqlite3
from pathlib import Path
from typing import Any

from services.verification.config import Factory


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS factories (
                id TEXT PRIMARY KEY, bundle TEXT NOT NULL, next_block INTEGER NOT NULL,
                retry_at INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS jobs (
                chain INTEGER NOT NULL, address TEXT NOT NULL, factory TEXT NOT NULL,
                tx TEXT NOT NULL, block INTEGER NOT NULL,
                sourcify INTEGER NOT NULL DEFAULT 0, explorer INTEGER NOT NULL DEFAULT 0,
                ticket TEXT NOT NULL DEFAULT '', retry_at INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (chain, address)
            );
            CREATE TABLE IF NOT EXISTS cooldowns (service TEXT PRIMARY KEY, until INTEGER NOT NULL);
        """)

    def close(self) -> None:
        self.db.close()

    def factory(self, spec: Factory) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM factories WHERE id = ?", (spec.key,)).fetchone()

    def bootstrap(self, spec: Factory, bundle: dict[str, Any]) -> None:
        first = int(bundle["deployment"]["blockNumber"])
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO factories (id, bundle, next_block) "
                            "VALUES (?, ?, ?)",
                            (spec.key, json.dumps(bundle), first))

    def discovered(self, spec: Factory, next_block: int, jobs: list[tuple]) -> None:
        # A crash records both the events and their cursor, or neither of them.
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO jobs "
                                "(chain, address, factory, tx, block) VALUES (?, ?, ?, ?, ?)", jobs)
            self.db.execute("UPDATE factories SET next_block = ?, retry_at = 0, failures = 0, "
                            "error = '' WHERE id = ?", (next_block, spec.key))

    def due(self, now: int, limit: int, factories: tuple[str, ...]) -> list[sqlite3.Row]:
        if not factories:
            return []
        return self.db.execute(
            "SELECT * FROM jobs WHERE sourcify = 0 "
            "AND factory IN (SELECT value FROM json_each(?)) AND retry_at <= ? "
            "ORDER BY retry_at, chain, block LIMIT ?",
            (json.dumps(factories), now, limit)).fetchall()

    def save_job(self, chain: int, address: str, *, sourcify: bool, explorer: bool,
                 ticket: str, retry_at: int, failures: int, error: str = "") -> None:
        with self.db:
            self.db.execute("UPDATE jobs SET sourcify = ?, explorer = ?, ticket = ?, "
                            "retry_at = ?, failures = ?, error = ? WHERE chain = ? AND address = ?",
                            (sourcify, explorer, ticket, retry_at, failures, error, chain, address))

    def defer_factory(self, spec: Factory, now: int, error: str) -> None:
        with self.db:
            self.db.execute("UPDATE factories SET retry_at = ?, failures = failures + 1, "
                            "error = ? WHERE id = ?", (now + 300, error, spec.key))

    def cooldown(self, service: str) -> int:
        row = self.db.execute("SELECT until FROM cooldowns WHERE service = ?",
                              (service,)).fetchone()
        return row[0] if row else 0

    def cool(self, service: str, until: int) -> None:
        with self.db:
            self.db.execute("INSERT INTO cooldowns VALUES (?, ?) ON CONFLICT(service) DO UPDATE "
                            "SET until = MAX(until, excluded.until)", (service, until))

    def summary(self) -> dict[str, int]:
        row = self.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(sourcify), 0), COALESCE(SUM(explorer), 0), "
            "COALESCE(SUM(sourcify = 0), 0) FROM jobs").fetchone()
        return dict(zip(("discovered", "sourcify", "explorer", "pending"), row, strict=True))
