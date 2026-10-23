"""The value pasted into DecisionVerifier on Arc: made here, checked by the contract in
contracts/test/DecisionVerifier.t.sol from the same fixture file."""

import json
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest
from eth_abi import decode

from agent.guardrails.rules import OwnerAnswer, Verdict, fingerprint
from agent.memory import SqliteJournal, remember
from agent.memory.sqlite import export_journal
from services.anchor import Anchor, AuditError, journal_root
from services.anchor.decision import make_decision_bundle
from services.anchor.onchain import PROOF_TYPE, arc_proof
from services.payments import PaymentStatus
from test_decision_proof import POLICY, SHOP, T0, payment, rule, session

FIXTURE = Path(__file__).parent.parent / "contracts" / "test" / "fixtures" / "decision-proof.json"


def journal() -> dict:
    """The synthetic journal of test_decision_proof: s1 paid PINV-1 after the owner approved."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "journal.sqlite3"
        first, other = rule("PINV-1"), rule("PINV-2")
        with SqliteJournal(path) as j:
            remember(j, [first, other], POLICY, at=T0)
            j.record_session(session("s0", T0 + timedelta(minutes=1), first, other))
            j.record_answer(OwnerAnswer("PINV-1", fingerprint(first), Verdict.APPROVE, "owner",
                                        T0 + timedelta(minutes=2), ""))
            j.record_payment(payment(PaymentStatus.SUBMITTED, T0 + timedelta(minutes=3), "s1"))
            j.record_payment(payment(PaymentStatus.COMPLETE, T0 + timedelta(minutes=4), "s1"))
            j.record_session(session("s1", T0 + timedelta(minutes=3), first))
        return {"shop": SHOP, "entries": export_journal(path)}


def fixture() -> dict:
    """Two anchors, the second covering the decision; the proof for anchor 1."""
    data = journal()
    hashes = [e["hash"] for e in data["entries"]]
    anchors = [Anchor(journal_root(hashes[:3]).hex(), 3, T0),
               Anchor(journal_root(hashes).hex(), len(hashes), T0)]
    bundle = make_decision_bundle(data, "s1", "PINV-1", anchors)
    return {
        "shop": data["shop"],
        "anchors": [{"root": "0x" + a.root, "size": a.size} for a in anchors],
        "proof": arc_proof(bundle, anchors),
        "expected": {"invoice": "PINV-1", "decision": "PAY_NOW", "reason": "ok",
                     "amount": "125.5", "supplier": "S", "payment": "COMPLETE", "paymentTx": "",
                     "decidedAt": (T0 + timedelta(minutes=3)).isoformat(), "records": 8},
    }


def test_the_contract_fixture_is_what_python_makes_now():
    """Regenerate with: uv run python -c "import sys; sys.path.insert(0, 'tests');
    import test_arc_proof as t; t.write()" when the journal format changes."""
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == fixture()


def test_the_dashboard_fixture_holds_the_value_python_makes():
    """dashboard/test/arc-check.test.mjs checks the browser makes this same value."""
    dash = json.loads((Path(__file__).parent.parent / "dashboard" / "test"
                       / "decision-fixture.json").read_text(encoding="utf-8"))
    anchors = [Anchor(a["root"], a["size"], None) for a in dash["anchors"]]
    assert dash["arcValue"] == arc_proof(dash["bundle"], anchors)


def test_the_value_holds_the_manifest_its_path_and_its_anchor():
    made = fixture()
    shop, position, index, path, text = decode([PROOF_TYPE], bytes.fromhex(made["proof"][2:]))[0]
    assert (shop.lower(), position) == (SHOP, 1)
    assert text.decode().startswith("embco-journal-v1\n")
    assert '"kind":"MANIFEST"' in text.decode() and len(path) > 0 and index > 0


def test_a_decision_without_a_manifest_has_no_value_for_arc():
    data = journal()
    hashes = [e["hash"] for e in data["entries"]]
    anchors = [Anchor(journal_root(hashes).hex(), len(hashes), T0)]
    bundle = make_decision_bundle(data, "s1", "PINV-1", anchors)
    bundle["proofs"] = [p for p in bundle["proofs"] if p["entry"]["kind"] != "MANIFEST"]
    with pytest.raises(AuditError, match="no MANIFEST"):
        arc_proof(bundle, anchors)
    with pytest.raises(AuditError, match="not on Arc"):
        arc_proof(make_decision_bundle(data, "s1", "PINV-1", anchors), [])


def test_the_cli_prints_the_value_from_a_downloaded_decision_proof(tmp_path, monkeypatch, capsys):
    from services import cli
    from services.anchor import JournalAnchors
    from test_journal_anchor import REGISTRY, FakeRegistry

    data = journal()
    hashes = [e["hash"] for e in data["entries"]]
    anchors = [Anchor(journal_root(hashes).hex(), len(hashes), T0)]
    registry = FakeRegistry()
    registry.anchors.append((bytes.fromhex(anchors[0].root), len(hashes), int(T0.timestamp())))
    monkeypatch.setattr(cli, "ArcRpc", lambda _url: registry)
    monkeypatch.setattr(cli, "JournalAnchors",
                        lambda chain, shop: JournalAnchors(chain, shop, address=REGISTRY))
    path = tmp_path / "decision.json"
    path.write_text(json.dumps(make_decision_bundle(data, "s1", "PINV-1", anchors)),
                    encoding="utf-8")
    assert cli.main(["arc-proof", str(path), "--rpc-url", "http://node"]) == 0
    out = capsys.readouterr().out
    assert "Paste this in verify" in out and out.strip().endswith(
        arc_proof(make_decision_bundle(data, "s1", "PINV-1", anchors), anchors)[-40:])


def write() -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fixture(), indent=1) + "\n", encoding="utf-8", newline="\n")
