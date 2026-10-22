"""Every entry behind one decision of the agent, gathered by its session and proved against Arc."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from agent.guardrails.controls import Finding, Outcome
from agent.guardrails.rules import Action, Decision, OwnerAnswer, PolicyConfig, Verdict, fingerprint
from agent.memory import SqliteJournal, remember
from agent.memory.sqlite import export_journal
from agent.models import AgentDecision, Choice, Session, Wake
from services.anchor import Anchor, AuditError, journal_root
from services.anchor.decision import (
    check_decision_bundle,
    decision_entries,
    make_decision_bundle,
)
from services.payments import PaymentEvent, PaymentStatus

SHOP = "0x" + "5c" * 20
POLICY = PolicyConfig(max_per_payment=Decimal(5000), weekly_budget=Decimal(2500))
T0 = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)


def rule(invoice: str, action: Action = Action.PAY) -> Decision:
    return Decision(invoice=invoice, supplier="S", amount=Decimal("125.5"),
                    due_date=date(2026, 10, 30), action=action, reasons=("all controls passed",),
                    findings=(Finding("three_way_match", Outcome.PASS, "ok"),))


def payment(status: PaymentStatus, at: datetime, session: str | None) -> PaymentEvent:
    return PaymentEvent(invoice="PINV-1", status=status, at=at, supplier="S", payee="0xabc",
                        amount=Decimal("125.5"), invoice_ref="0x01", circle_tx_id="c1",
                        session=session)


def session(sid: str, at: datetime, *decided: Decision) -> Session:
    return Session(at=at, wakes=(Wake("start", "s"),), finished=True, id=sid,
                   decisions=tuple(AgentDecision(d.invoice, fingerprint(d), Choice.PAY_NOW, "ok")
                                   for d in decided))


@pytest.fixture
def data(tmp_path):
    """A check of two invoices, an earlier session that decided PINV-1 too, the owner's answer,
    then a session that paid PINV-1: its payments are written before the session, as live."""
    path = tmp_path / "journal.sqlite3"
    first, other = rule("PINV-1"), rule("PINV-2")
    with SqliteJournal(path) as journal:
        remember(journal, [first, other], POLICY, at=T0)
        journal.record_session(session("s0", T0 + timedelta(minutes=1), first, other))
        journal.record_answer(OwnerAnswer("PINV-1", fingerprint(first), Verdict.APPROVE,
                                          "owner", T0 + timedelta(minutes=2), ""))
        journal.record_payment(payment(PaymentStatus.SUBMITTED, T0 + timedelta(minutes=3), "s1"))
        journal.record_payment(payment(PaymentStatus.COMPLETE, T0 + timedelta(minutes=4), "s1"))
        journal.record_session(session("s1", T0 + timedelta(minutes=3), first))
        journal.record_payment(payment(PaymentStatus.RECORDED, T0 + timedelta(minutes=5), "s1"))
    return {"shop": SHOP, "entries": export_journal(path)}


def anchors_of(data):
    hashes = [e["hash"] for e in data["entries"]]
    return [Anchor(journal_root(hashes).hex(), len(hashes), T0 + timedelta(hours=1))]


def kinds(data, numbers):
    return [data["entries"][n - 1]["kind"] for n in numbers]


def test_the_entries_of_a_decision_are_its_session_check_policy_answer_and_payments(data):
    numbers = decision_entries(data["entries"], "s1", "PINV-1")
    assert kinds(data, numbers) == ["POLICY", "RUN", "DECISION", "ANSWER", "PAYMENT", "PAYMENT",
                                    "SESSION", "AGENT", "PAYMENT"]
    # the earlier session's choice on PINV-1 and anything about PINV-2 are left out
    assert all(data["entries"][n - 1]["invoice"] != "PINV-2" for n in numbers)


def test_a_decision_proof_checks_every_entry_against_arc(data):
    anchors = anchors_of(data)
    bundle = json.loads(json.dumps(make_decision_bundle(data, "s1", "PINV-1", anchors)))
    result = check_decision_bundle(bundle, anchors)
    assert result.ok and len(result.proofs) == 9


def test_an_entry_from_another_decision_is_caught(data):
    anchors = anchors_of(data)
    bundle = make_decision_bundle(data, "s1", "PINV-1", anchors)
    other = make_decision_bundle(data, "s0", "PINV-2", anchors)
    stray = next(p for p in other["proofs"] if p["entry"]["kind"] == "DECISION")
    bundle["proofs"].append(stray)
    result = check_decision_bundle(bundle, anchors)
    assert not result.ok and "DECISION entry" in result.unlinked


def test_a_changed_entry_fails_its_proof(data):
    anchors = anchors_of(data)
    bundle = make_decision_bundle(data, "s1", "PINV-1", anchors)
    agent = next(p for p in bundle["proofs"] if p["entry"]["kind"] == "AGENT")
    agent["entry"]["body"] = agent["entry"]["body"].replace('"ok"', '"changed"')
    result = check_decision_bundle(bundle, anchors)
    assert not result.ok and result.unlinked is None
    assert [p.content_matches_hash for p in result.proofs].count(False) == 1


def test_a_session_from_before_ids_cannot_be_gathered(tmp_path):
    path = tmp_path / "journal.sqlite3"
    with SqliteJournal(path) as journal:
        journal.record_session(session("", T0, rule("PINV-1")))
    entries = export_journal(path)
    assert "session" not in json.loads(entries[-1]["body"])
    with pytest.raises(AuditError, match="no decision on PINV-1"):
        decision_entries(entries, "", "PINV-1")


def test_the_cli_checks_the_decision_proof_the_dashboard_downloads(tmp_path, monkeypatch, capsys):
    """The browser makes this same bundle (dashboard/test/proof.test.mjs checks it is equal)."""
    from pathlib import Path

    from services import cli
    from services.anchor import JournalAnchors
    from test_journal_anchor import REGISTRY, FakeRegistry

    fixture = json.loads((Path(__file__).parent.parent / "dashboard" / "test"
                          / "decision-fixture.json").read_text(encoding="utf-8"))
    registry = FakeRegistry()
    for a in fixture["anchors"]:
        registry.anchors.append((bytes.fromhex(a["root"]), a["size"], int(T0.timestamp())))
    monkeypatch.setattr(cli, "ArcRpc", lambda _url: registry)
    monkeypatch.setattr(cli, "JournalAnchors",
                        lambda chain, shop: JournalAnchors(chain, shop, address=REGISTRY))
    path = tmp_path / "decision.json"
    path.write_text(json.dumps(fixture["bundle"]), encoding="utf-8")
    assert cli.main(["check-proof", str(path), "--rpc-url", "http://node"]) == 0
    assert "every entry of this decision was anchored on Arc" in capsys.readouterr().out
    bundle = fixture["bundle"]
    bundle["proofs"][-1]["entry"]["body"] = '{"forged":true}'
    path.write_text(json.dumps(bundle), encoding="utf-8")
    assert cli.main(["check-proof", str(path), "--rpc-url", "http://node"]) == 1
    assert "DOES NOT CHECK" in capsys.readouterr().out


def test_an_entry_not_anchored_yet_gives_no_decision_proof(data):
    hashes = [e["hash"] for e in data["entries"]][:3]
    early = [Anchor(journal_root(hashes).hex(), 3, T0)]
    with pytest.raises(AuditError, match="not covered by an anchor on Arc yet"):
        make_decision_bundle(data, "s1", "PINV-1", early)
