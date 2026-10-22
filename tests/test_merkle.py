"""The journal's Merkle root follows RFC 9162; checked against the Certificate Transparency
reference test vectors (the eight leaves used by the RFC 6962 / CT implementations)."""

import hashlib
from datetime import UTC, datetime

from agent.memory import SqliteJournal
from agent.memory.merkle import leaf_hash, node_hash, root

LEAVES = [b"", b"\x00", b"\x10", b"\x20\x21", b"\x30\x31", b"\x40\x41\x42\x43",
          bytes(range(0x50, 0x58)), bytes(range(0x60, 0x70))]
ROOTS = [
    "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
    "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125",
    "aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77",
    "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
    "4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4",
    "76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef",
    "ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c",
    "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328",
]


def test_empty_tree_is_the_hash_of_nothing():
    assert root([]) == hashlib.sha256(b"").digest()


def test_roots_match_the_ct_reference_vectors():
    for size, expected in enumerate(ROOTS, start=1):
        assert root(LEAVES[:size]).hex() == expected, f"size {size}"


def test_a_leaf_cannot_pass_for_a_node():
    a, b = leaf_hash(b"a"), leaf_hash(b"b")
    assert root([a + b]) != node_hash(a, b)


def test_any_change_or_reorder_changes_the_root():
    base = root(LEAVES)
    assert root([*LEAVES[:3], b"\xff", *LEAVES[4:]]) != base
    assert root([LEAVES[1], LEAVES[0], *LEAVES[2:]]) != base
    assert root(LEAVES[:-1]) != base


def test_journal_gives_its_entry_hashes_in_order(tmp_path):
    with SqliteJournal(tmp_path / "j.sqlite3") as journal:
        at = datetime(2026, 10, 21, 5, 48, tzinfo=UTC)
        journal._append_owner_entry("ANSWER", at, {"note": "first"}, "INV-1")
        journal._append_owner_entry("ANSWER", at, {"note": "second"}, "INV-2")
        hashes = journal.entry_hashes()
        journal.verify()
    assert len(hashes) == 2 and all(len(h) == 64 for h in hashes)
    assert hashes[0] != hashes[1]
