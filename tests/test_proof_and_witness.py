"""One entry proved against Arc (RFC 9162 inclusion, the chain of that entry, the root on Arc),
and the witness that records when each anchor was first seen and compares it with a copy."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from agent.memory import SqliteJournal
from agent.memory.merkle import inclusion_proof, verify_inclusion
from agent.memory.sqlite import export_journal
from services import cli
from services.anchor import Anchor, AuditError, JournalAnchors, journal_root
from services.anchor.proof import check_bundle, make_bundle, proof_report, read_bundle
from services.anchor.witness import load_state, save_state, witness
from test_journal_anchor import REGISTRY, SHOP, FakeRegistry

T0 = datetime(2026, 10, 21, 5, 48, tzinfo=UTC)
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


@pytest.fixture
def journal_file(tmp_path):
    """A downloaded journal of five entries, with accents and quotes."""
    path = tmp_path / "journal.sqlite3"
    with SqliteJournal(path) as journal:
        for i, note in enumerate(["uno", 'dos "comillas"', "café ñandú", "cuatro", "cinco"]):
            journal._append_owner_entry("ANSWER", T0 + timedelta(seconds=i), {"note": note},
                                        f"INV-{i}")
    return {"shop": SHOP, "entries": export_journal(path)}


def anchor_of(data, size, at=NOW):
    return Anchor(journal_root([e["hash"] for e in data["entries"][:size]]).hex(), size, at)


# ---- the Merkle inclusion proof


def test_every_leaf_of_every_small_tree_has_a_proof_that_checks_against_the_root():
    for n in range(1, 40):
        leaves = [bytes([i, n]) for i in range(n)]
        root = journal_root([leaf.hex() for leaf in leaves])  # any root of this shape
        for m in range(n):
            proof = inclusion_proof(leaves, m)
            from agent.memory.merkle import root as tree_root
            expected = tree_root(leaves)
            assert verify_inclusion(leaves[m], m, n, proof, expected), (n, m)
            assert not verify_inclusion(b"forged", m, n, proof, expected), (n, m)
        assert root is not None


def test_a_proof_fails_for_another_index_size_or_root():
    leaves = [bytes([i]) for i in range(7)]
    from agent.memory.merkle import root as tree_root
    expected = tree_root(leaves)
    proof = inclusion_proof(leaves, 3)
    assert not verify_inclusion(leaves[3], 2, 7, proof, expected)
    # the size is bound by the anchor on Arc (check_bundle), not by the root alone
    assert not verify_inclusion(leaves[3], 3, 7, proof, b"\x00" * 32)
    assert not verify_inclusion(leaves[3], 9, 7, proof, expected)


# ---- the proof of one entry


def test_a_proof_of_an_entry_checks_its_content_its_place_and_arc(journal_file):
    anchors = [anchor_of(journal_file, 3), anchor_of(journal_file, 5)]
    bundle = make_bundle(journal_file, 2, anchors)
    assert bundle["size"] == 3  # the first anchor that covers entry 2
    assert bundle["entry"]["body"] == '{"note":"dos \\"comillas\\""}'
    result = check_bundle(json.loads(json.dumps(bundle)), anchors)
    assert result.ok and result.anchored_at == NOW.isoformat()
    text = proof_report(bundle, result)
    assert "Its text:" in text and "anchored on Arc, unchanged" in text


def test_a_proof_is_refused_for_an_entry_no_anchor_covers(journal_file):
    with pytest.raises(AuditError, match="not covered by an anchor on Arc yet"):
        make_bundle(journal_file, 5, [anchor_of(journal_file, 3)])
    with pytest.raises(AuditError, match="entries 1 to 5"):
        make_bundle(journal_file, 9, [anchor_of(journal_file, 5)])


def test_a_copy_that_does_not_match_arc_gives_no_proof(journal_file):
    wrong = Anchor("00" * 32, 3, NOW)
    with pytest.raises(AuditError, match="do not match Arc"):
        make_bundle(journal_file, 2, [wrong])


def test_a_changed_entry_fails_the_content_check(journal_file):
    anchors = [anchor_of(journal_file, 5)]
    bundle = make_bundle(journal_file, 2, anchors)
    bundle["entry"]["body"] = '{"note":"forged"}'
    result = check_bundle(bundle, anchors)
    assert not result.content_matches_hash and not result.ok
    assert "DOES NOT CHECK" in proof_report(bundle, result)


def test_a_bundle_that_claims_a_root_arc_never_wrote_fails(journal_file):
    anchors = [anchor_of(journal_file, 5)]
    bundle = make_bundle(journal_file, 2, anchors)
    bundle["root"] = "11" * 32  # the prover's own claim
    result = check_bundle(bundle, anchors)
    assert not result.included and not result.anchored and not result.ok


def test_a_bundle_with_the_wrong_size_is_not_anchored(journal_file):
    anchors = [anchor_of(journal_file, 5)]
    bundle = make_bundle(journal_file, 2, anchors)
    bundle['size'] = 4  # the same root, claimed for a smaller tree
    result = check_bundle(bundle, anchors)
    assert not result.anchored and not result.ok


def test_a_rewritten_journal_cannot_produce_a_proof_that_checks(journal_file):
    """Rewrite entry 2 and recompute the chain: the proof is made from the copy, so it
    fails at Arc."""
    from agent.memory.models import GENESIS
    from agent.memory.sqlite import entry_hash_of
    anchors = [anchor_of(journal_file, 5)]  # what Arc holds, from the honest journal
    forged = json.loads(json.dumps(journal_file))
    forged["entries"][1]["body"] = '{"note":"forged"}'
    previous = GENESIS
    for e in forged["entries"]:
        e["hash"] = entry_hash_of(previous, e)
        previous = e["hash"]
    with pytest.raises(AuditError, match="do not match Arc"):
        make_bundle(forged, 2, anchors)  # the forger cannot make a proof that Arc accepts


def test_read_bundle_refuses_other_json(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('{"hello": 1}', encoding="utf-8")
    with pytest.raises(AuditError, match="not a proof file"):
        read_bundle(str(path))


def test_cli_prove_and_check_proof(journal_file, tmp_path, monkeypatch, capsys):
    path = tmp_path / "download.json"
    path.write_text(json.dumps(journal_file, ensure_ascii=False), encoding="utf-8")
    registry = FakeRegistry()
    registry.anchors.append((journal_root([e["hash"] for e in journal_file["entries"]]), 5,
                             int(NOW.timestamp())))
    monkeypatch.setattr(cli, "ArcRpc", lambda _url: registry)
    monkeypatch.setattr(cli, "JournalAnchors",
                        lambda chain, shop: JournalAnchors(chain, shop, address=REGISTRY))
    out = tmp_path / "proof.json"
    assert cli.main(["prove", str(path), "--entry", "4", "--out", str(out),
                     "--rpc-url", "http://node"]) == 0
    assert cli.main(["check-proof", str(out), "--rpc-url", "http://node"]) == 0
    assert "anchored on Arc, unchanged" in capsys.readouterr().out
    bundle = json.loads(out.read_text(encoding="utf-8"))
    bundle["entry"]["body"] = '{"note":"forged"}'
    out.write_text(json.dumps(bundle), encoding="utf-8")
    assert cli.main(["check-proof", str(out), "--rpc-url", "http://node"]) == 1
    assert "DOES NOT CHECK" in capsys.readouterr().out


# ---- the witness


def test_a_new_anchor_matching_the_copy_is_recorded_without_alert(journal_file, tmp_path):
    state = {"shop": SHOP, "seen": []}
    run = witness(state, [anchor_of(journal_file, 3)], journal_file, NOW)
    assert run.ok and run.anchors_seen == 1
    assert state["seen"][0]["first_seen"] == NOW.isoformat()
    assert "matches your copy" in run.notes[0]


def test_an_anchor_newer_than_the_copy_is_not_checked_yet(journal_file):
    short = {"shop": SHOP, "entries": journal_file["entries"][:2]}
    run = witness({"shop": SHOP, "seen": []}, [anchor_of(journal_file, 4)], short, NOW)
    assert run.ok and "not checked yet" in run.notes[0]


def test_an_anchor_that_does_not_match_the_copy_raises_an_alert(journal_file):
    state = {"shop": SHOP, "seen": []}
    forged = Anchor("00" * 32, 3, NOW)
    run = witness(state, [forged], journal_file, NOW)
    assert not run.ok and "does NOT match your copy" in run.alerts[0]


def test_an_anchor_seen_earlier_that_changed_or_vanished_raises_an_alert(journal_file):
    state = {"shop": SHOP, "seen": []}
    witness(state, [anchor_of(journal_file, 3)], journal_file, NOW)
    changed = witness(state, [Anchor("ab" * 32, 3, NOW)], journal_file, NOW + timedelta(days=1))
    assert any("changed since it was seen" in a for a in changed.alerts)
    gone = witness(state, [], journal_file, NOW + timedelta(days=2))
    assert any("no longer on Arc" in a for a in gone.alerts)


def test_the_witness_record_is_kept_between_runs_and_not_duplicated(journal_file, tmp_path):
    path = tmp_path / "witness.json"
    state = load_state(path, SHOP)
    anchors = [anchor_of(journal_file, 3)]
    witness(state, anchors, journal_file, NOW)
    save_state(path, state)
    again = load_state(path, SHOP)
    run = witness(again, anchors, journal_file, NOW + timedelta(days=1))
    assert run.ok and run.anchors_seen == 1
    assert again["seen"][0]["first_seen"] == NOW.isoformat()  # the first sighting is kept


def test_a_record_of_another_shop_is_refused(tmp_path):
    path = tmp_path / "witness.json"
    save_state(path, {"shop": "0x" + "11" * 20, "seen": []})
    with pytest.raises(AuditError, match="another shop"):
        load_state(path, SHOP)


def test_cli_witness_exits_by_the_result(journal_file, tmp_path, monkeypatch, capsys):
    path = tmp_path / "copy.json"
    path.write_text(json.dumps(journal_file, ensure_ascii=False), encoding="utf-8")
    registry = FakeRegistry()
    registry.anchors.append((journal_root([e["hash"] for e in journal_file["entries"]]), 5,
                             int(NOW.timestamp())))
    monkeypatch.setattr(cli, "ArcRpc", lambda _url: registry)
    monkeypatch.setattr(cli, "JournalAnchors",
                        lambda chain, shop: JournalAnchors(chain, shop, address=REGISTRY))
    state = tmp_path / "w.json"
    args = ["witness", "--shop", SHOP, "--journal", str(path), "--state", str(state),
            "--rpc-url", "http://node"]
    assert cli.main(args) == 0
    assert "1 anchors recorded" in capsys.readouterr().out
    registry.anchors[0] = (b"\x99" * 32, 5, int(NOW.timestamp()))
    assert cli.main(args) == 1
    assert "ALERT" in capsys.readouterr().out
