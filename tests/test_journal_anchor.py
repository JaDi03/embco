"""The journal anchored on Arc: the root written by the agent, the check of a copy against it
(also a rewrite that keeps the hash chain valid), and when the agent anchors."""

import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from eth_abi import decode, encode
from eth_utils import keccak

from agent.memory import SqliteJournal
from agent.memory.models import GENESIS, chain_hash
from agent.memory.sqlite import _content
from services import cli
from services.anchor import Anchor, JournalAnchors, journal_root, mismatches
from services.circle import CircleError, CircleTransaction
from services.payments import ChainError
from services.shops.anchoring import anchor_journal, anchors_for, due, money_moved

SHOP = "0x" + "5c" * 20
AGENT = "0xeAc91C9491F0D391c80f17C4267A3329Ad7Ba862"
REGISTRY = "0x" + "a7" * 20
NOW = datetime(2026, 10, 21, 5, 50, tzinfo=UTC)
T0 = datetime(2026, 10, 21, 5, 48, tzinfo=UTC)


def _sel(sig: str) -> bytes:
    return keccak(text=sig)[:4]


class FakeRegistry:
    """The JournalAnchor contract on a fake Arc node, with the same rules as the Solidity one."""

    def __init__(self) -> None:
        self.anchors: list[tuple[bytes, int, int]] = []
        self.simulated: list[str | None] = []

    def call(self, to: str, data: bytes, *, sender: str | None = None) -> bytes:
        sel, args = data[:4], data[4:]
        if sel == _sel("count(address)"):
            return encode(["uint256"], [len(self.anchors)])
        if sel == _sel("latest(address)"):
            last = self.anchors[-1] if self.anchors else (b"\x00" * 32, 0, 0)
            return encode(["(bytes32,uint64,uint64)"], [last])
        if sel == _sel("anchorAt(address,uint256)"):
            (_, i) = decode(["address", "uint256"], args)
            return encode(["(bytes32,uint64,uint64)"], [self.anchors[i]])
        if sel == _sel("anchor(address,bytes32,uint64)"):
            self.simulated.append(sender)
            self.check(sender, decode(["address", "bytes32", "uint64"], args))
            return b""
        raise AssertionError(f"unexpected call {sel.hex()}")

    def check(self, sender, args) -> None:
        (_, _root, size) = args
        if sender != AGENT:
            raise ChainError("reverted: NotShopAgent")
        if size <= (self.anchors[-1][1] if self.anchors else 0):
            raise ChainError("reverted: SizeMustGrow")


class FakeCircle:
    def __init__(self, registry: FakeRegistry, *, fail: bool = False) -> None:
        self.registry = registry
        self.keys: list[str] = []
        self.fail = fail

    def execute_contract(self, wallet_id, contract, call_data, *, idempotency_key):
        self.keys.append(idempotency_key)
        (_, root, size) = decode(["address", "bytes32", "uint64"], call_data[4:])
        if not self.fail:
            self.registry.anchors.append((root, size, int(NOW.timestamp())))
        return CircleTransaction(id="tx-1", state="INITIATED")

    def get_transaction(self, transaction_id):
        if self.fail:
            return CircleTransaction(id="tx-1", state="FAILED", error_reason="out of gas")
        return CircleTransaction(id="tx-1", state="COMPLETE", tx_hash="0xabc")


def _payer(registry: FakeRegistry, **circle) -> SimpleNamespace:
    return SimpleNamespace(chain=registry, circle=FakeCircle(registry, **circle),
                           wallet_id="w-1", shop=SHOP, agent_address=lambda: AGENT)


def _anchors(registry: FakeRegistry, **circle) -> JournalAnchors:
    return JournalAnchors(registry, SHOP, address=REGISTRY, payer=_payer(registry, **circle),
                          sleep=lambda _s: None)


def _journal(tmp_path, notes=("a", "b", "c")) -> SqliteJournal:
    journal = SqliteJournal(tmp_path / "journal.sqlite3")
    for i, note in enumerate(notes):
        journal._append_owner_entry("ANSWER", T0 + timedelta(seconds=i), {"note": note}, "INV-1")
    return journal


def test_the_agent_anchors_the_root_of_the_whole_journal(tmp_path):
    registry = FakeRegistry()
    anchors = _anchors(registry)
    with _journal(tmp_path) as journal:
        hashes = journal.entry_hashes()
        done = anchors.write(journal_root(hashes), len(hashes))
    assert registry.simulated == [AGENT]  # simulated as the agent before sending
    assert done["tx"] == "0xabc" and done["size"] == 3
    last = anchors.latest()
    assert (last.root, last.size) == (journal_root(hashes).hex(), 3)
    assert last.anchored_at == NOW


def test_a_retry_uses_the_same_idempotency_key(tmp_path):
    registry = FakeRegistry()
    anchors = _anchors(registry)
    root = b"\x11" * 32
    anchors.write(root, 5)
    registry.anchors.clear()
    anchors.write(root, 5)
    keys = anchors._payer.circle.keys
    assert keys[0] == keys[1]


def test_a_failed_anchor_says_why():
    anchors = _anchors(FakeRegistry(), fail=True)
    with pytest.raises(CircleError, match="out of gas"):
        anchors.write(b"\x11" * 32, 5)


def test_a_shrinking_journal_cannot_be_anchored():
    registry = FakeRegistry()
    anchors = _anchors(registry)
    anchors.write(b"\x11" * 32, 5)
    with pytest.raises(ChainError, match="SizeMustGrow"):
        anchors.write(b"\x22" * 32, 4)


def test_the_untouched_copy_matches_every_anchor(tmp_path):
    with _journal(tmp_path) as journal:
        hashes = journal.entry_hashes()
    anchors = [Anchor(journal_root(hashes[:2]).hex(), 2, NOW),
               Anchor(journal_root(hashes).hex(), 3, NOW)]
    assert mismatches(hashes, anchors) == []
    # a journal that kept growing after the last anchor still matches
    assert mismatches([*hashes, "ab" * 32], anchors) == []


def test_a_rewrite_that_keeps_the_chain_valid_is_caught_by_the_anchor(tmp_path):
    """The attack the chain alone cannot see: edit an entry, then recompute every hash."""
    with _journal(tmp_path) as journal:
        anchored = [Anchor(journal_root(journal.entry_hashes()).hex(), 3, NOW)]
    con = sqlite3.connect(tmp_path / "journal.sqlite3", isolation_level=None)
    con.execute("drop trigger entries_no_update")
    con.execute("update entries set body = ? where id = 2", ('{"note":"forged"}',))
    previous = GENESIS
    for rid, kind, run, invoice, at, body in con.execute(
            "select id, kind, run, invoice, at, body from entries order by id").fetchall():
        previous = chain_hash(previous, _content(kind, run, invoice, at, body))
        con.execute("update entries set entry_hash = ? where id = ?", (previous, rid))
    con.close()
    with SqliteJournal(tmp_path / "journal.sqlite3") as journal:
        journal.verify()  # the chain alone says nothing changed
        problems = mismatches(journal.entry_hashes(), anchored)
    assert len(problems) == 1 and "do not give the root" in problems[0]


def test_removed_entries_are_caught(tmp_path):
    with _journal(tmp_path) as journal:
        hashes = journal.entry_hashes()
    anchored = [Anchor(journal_root(hashes).hex(), 3, NOW)]
    assert "entries were removed" in mismatches(hashes[:2], anchored)[0]


def _entries(*kinds_and_bodies):
    return [(kind, None, None, T0, body) for kind, body in kinds_and_bodies]


def test_money_moved_means_a_payment_step_or_a_session_that_moved_funds():
    assert money_moved(_entries(("PAYMENT", {"status": "submitted"})))
    assert money_moved(_entries(("SESSION", {"funds_moved": [{"kind": "earn_park"}]})))
    assert not money_moved(_entries(("SESSION", {"funds_moved": []}), ("RUN", {})))


def test_anchoring_is_due_after_money_moved_or_once_a_day():
    quiet = _entries(("RUN", {}), ("RUN", {}), ("RUN", {}))
    moved = _entries(("RUN", {}), ("RUN", {}), ("PAYMENT", {}))
    recent = Anchor("ab" * 32, 2, NOW - timedelta(hours=2))
    old = Anchor("ab" * 32, 2, NOW - timedelta(hours=25))
    assert due(recent, moved, NOW)
    assert not due(recent, quiet, NOW)
    assert due(old, quiet, NOW)
    assert due(Anchor("", 0, None), quiet, NOW)  # never anchored
    assert not due(Anchor("ab" * 32, 3, None), quiet, NOW)  # nothing new


def test_anchor_journal_writes_and_reports_it(tmp_path):
    registry = FakeRegistry()
    with _journal(tmp_path) as journal:
        event = anchor_journal(journal, _anchors(registry), NOW)
    assert event["kind"] == "anchor" and event["anchor_tx"] == "0xabc"
    assert "tx" not in event  # not shown as money that moved
    assert "3 entries" in event["text"]
    assert len(registry.anchors) == 1


def test_a_failed_anchor_never_stops_the_check(tmp_path):
    with _journal(tmp_path) as journal:
        assert anchor_journal(journal, _anchors(FakeRegistry(), fail=True), NOW) is None
        assert anchor_journal(journal, None, NOW) is None


def test_no_registry_or_no_payments_means_no_anchoring():
    registry = FakeRegistry()
    assert anchors_for(None, REGISTRY) is None
    assert anchors_for(_payer(registry), "") is None
    assert anchors_for(_payer(registry), REGISTRY) is not None


def test_cli_verify_checks_the_copy_against_arc(tmp_path, monkeypatch, capsys):
    registry = FakeRegistry()
    with _journal(tmp_path) as journal:
        hashes = journal.entry_hashes()
        registry.anchors.append((journal_root(hashes), 3, int(NOW.timestamp())))
        monkeypatch.setattr(cli, "JOURNAL_ANCHOR", REGISTRY)
        monkeypatch.setattr(cli, "ArcRpc", lambda _url: registry)
        monkeypatch.setattr(cli, "JournalAnchors",
                            lambda chain, shop: JournalAnchors(chain, shop, address=REGISTRY))
        settings = SimpleNamespace(journal_path=tmp_path / "journal.sqlite3",
                                   arc_rpc_url="http://node", shop_address=SHOP)
        assert cli._verify(None, settings, journal) == 0
        assert "matches all 1 anchors on Arc: 3 of 3 entries" in capsys.readouterr().out
        registry.anchors[0] = (b"\x99" * 32, 3, int(NOW.timestamp()))
        assert cli._verify(None, settings, journal) == 1
        assert "DOES NOT MATCH ARC" in capsys.readouterr().err
