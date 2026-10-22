"""Auditing a downloaded journal: the chain and the roots recomputed from the file, compared
with what is on Arc (never with what the file says about Arc)."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from agent.memory import SqliteJournal
from agent.memory.models import GENESIS, chain_hash
from agent.memory.sqlite import _content, export_journal, verify_export
from services import cli
from services.anchor import Anchor, AuditError, JournalAnchors, audit, journal_root, report
from services.anchor.audit import load
from test_journal_anchor import REGISTRY, SHOP, FakeRegistry

T0 = datetime(2026, 10, 21, 5, 48, tzinfo=UTC)
ANCHORED = datetime(2026, 10, 22, 8, 20, tzinfo=UTC)


@pytest.fixture
def exported(tmp_path):
    """A downloaded journal of four entries, text with accents and quotes included."""
    path = tmp_path / "journal.sqlite3"
    with SqliteJournal(path) as journal:
        for i, note in enumerate(["uno", 'dos "comillas"', "café ñandú", "cuatro"]):
            journal._append_owner_entry("ANSWER", T0 + timedelta(seconds=i), {"note": note},
                                        f"INV-{i}")
    entries = export_journal(path)
    return {"shop": SHOP, "entries": entries}, tmp_path / "download.json"


def _anchor(entries, size):
    return Anchor(journal_root([e["hash"] for e in entries[:size]]).hex(), size, ANCHORED)


def test_an_untouched_download_matches_every_anchor(exported):
    data, _ = exported
    result = audit(data, [_anchor(data["entries"], 2), _anchor(data["entries"], 4)])
    assert result.ok and result.chain_problem is None and result.entries == 4
    assert [r.matches for r in result.rows] == [True, True]
    assert result.root == journal_root([e["hash"] for e in data["entries"]]).hex()
    assert "matches every anchor on Arc; the last covers 4 of its 4 entries" in report(result)


def test_a_journal_that_kept_growing_still_matches_the_older_anchors(exported):
    data, _ = exported
    result = audit(data, [_anchor(data["entries"], 2)])
    assert result.ok and "covers 2 of its 4 entries" in report(result)


def test_an_edit_is_caught_by_the_chain(exported):
    data, _ = exported
    anchored = _anchor(data["entries"], 4)
    data["entries"][1]["body"] = data["entries"][1]["body"].replace("dos", "DOS")
    result = audit(data, [anchored])
    assert not result.ok and result.chain_problem == "entry 2 (ANSWER) does not match its hash"
    assert "BROKEN" in report(result)


def test_a_rewrite_with_every_hash_recomputed_is_caught_by_the_anchor(exported):
    data, _ = exported
    anchored = _anchor(data["entries"], 4)  # what the agent wrote on Arc before the rewrite
    previous = GENESIS
    for e in data["entries"]:
        if e["invoice"] == "INV-1":
            e["body"] = '{"note":"forged"}'
        previous = e["hash"] = chain_hash(
            previous, _content(e["kind"], e["run"], e["invoice"], e["at"], e["body"]))
    assert verify_export(data["entries"]) is None  # the chain alone is fooled
    result = audit(data, [anchored])
    assert not result.ok and result.rows[0].matches is False
    assert "DIFFERENT" in report(result) and "DOES NOT MATCH" in report(result)


def test_removed_entries_are_caught(exported):
    data, _ = exported
    anchored = _anchor(data["entries"], 4)
    data["entries"] = data["entries"][:3]
    result = audit(data, [anchored])
    assert not result.ok and result.rows[0].file_root is None
    assert "MISSING ENTRIES" in report(result)


def test_what_the_file_says_about_arc_is_ignored(exported):
    data, _ = exported
    data["anchors_on_arc"] = [{"root": "00" * 32, "size": 4, "matches_this_file": True}]
    data["merkle"] = {"root": "00" * 32, "size": 4}
    result = audit(data, [_anchor(data["entries"], 4)])
    assert result.ok  # judged against the anchors read from Arc, not the file's own claims


def test_nothing_anchored_is_said_plainly(exported):
    result = audit(exported[0], [])
    assert result.ok and "nothing on Arc to compare with" in report(result)


def test_a_file_that_is_not_a_journal_is_refused(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('{"hello": 1}', encoding="utf-8")
    with pytest.raises(AuditError, match="not an embco journal"):
        load(path)
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(AuditError):
        load(path)


def test_cli_audit_reads_arc_and_exits_by_the_result(exported, monkeypatch, capsys):
    data, path = exported
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    registry = FakeRegistry()
    registry.anchors.append((journal_root([e["hash"] for e in data["entries"]]), 4,
                             int(ANCHORED.timestamp())))
    monkeypatch.setattr(cli, "ArcRpc", lambda _url: registry)
    monkeypatch.setattr(cli, "JournalAnchors",
                        lambda chain, shop: JournalAnchors(chain, shop, address=REGISTRY))
    assert cli.main(["audit", str(path), "--rpc-url", "http://node"]) == 0
    assert "RESULT: the file matches every anchor on Arc" in capsys.readouterr().out
    registry.anchors[0] = (b"\x99" * 32, 4, int(ANCHORED.timestamp()))
    assert cli.main(["audit", str(path), "--rpc-url", "http://node"]) == 1
    assert "DOES NOT MATCH" in capsys.readouterr().out


def test_cli_audit_needs_a_node_and_a_real_file(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("ARC_TESTNET_RPC_URL", raising=False)
    assert cli.main(["audit", str(tmp_path / "x.json")]) == 2
    assert "--rpc-url" in capsys.readouterr().err
    assert cli.main(["audit", str(tmp_path / "x.json"), "--rpc-url", "http://node"]) == 2
    assert "not an embco journal" in capsys.readouterr().err


def test_exporting_does_not_touch_the_journal(exported):
    _, path = exported
    db = path.parent / "journal.sqlite3"
    before = db.read_bytes()
    export_journal(db)
    assert db.read_bytes() == before
