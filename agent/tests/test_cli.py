from datetime import UTC, datetime
from decimal import Decimal

import pytest

from embco.cli import main
from embco.decision import PolicyConfig
from embco.journal import SqliteJournal, remember
from test_owner_answers import asked

CANARY = "canary-value-that-must-not-leak"


@pytest.fixture
def env(tmp_path, monkeypatch):
    journal = tmp_path / "data" / "journal.sqlite3"
    values = {
        "EMBCO_ERPNEXT_URL": "https://erp.example.com",
        "EMBCO_ERPNEXT_API_KEY": "key",
        "EMBCO_ERPNEXT_API_SECRET": CANARY,
        "EMBCO_COMPANY": "TEST Shop",
        "EMBCO_MAX_PER_PAYMENT": "500",
        "EMBCO_WEEKLY_BUDGET": "2500",
        "EMBCO_JOURNAL_PATH": str(journal),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return journal


def no_env_file(tmp_path):
    return ["--env-file", str(tmp_path / "missing.env")]


def test_missing_settings_exit_with_code_2_and_no_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EMBCO_ERPNEXT_API_SECRET", CANARY)
    for key in ("EMBCO_ERPNEXT_URL", "EMBCO_COMPANY", "EMBCO_JOURNAL_PATH"):
        monkeypatch.delenv(key, raising=False)
    assert main([*no_env_file(tmp_path), "verify"]) == 2
    err = capsys.readouterr().err
    assert "EMBCO_ERPNEXT_URL" in err
    assert CANARY not in err


def test_verify_creates_the_memory_folder_and_checks_it(env, tmp_path, capsys):
    assert main([*no_env_file(tmp_path), "verify"]) == 0
    assert env.exists()
    assert "journal verified" in capsys.readouterr().out


def test_the_owner_answers_and_reads_the_history(env, tmp_path, capsys):
    policy = PolicyConfig(max_per_payment=Decimal(500), weekly_budget=Decimal(2500))
    env.parent.mkdir(parents=True)
    with SqliteJournal(env) as journal:
        remember(journal, [asked()], policy, at=datetime(2026, 10, 12, tzinfo=UTC))
    args = no_env_file(tmp_path)
    assert main([*args, "answer", "PINV-1", "approve", "--by", "Ada", "--note", "ok"]) == 0
    assert "recorded: APPROVE PINV-1 by Ada" in capsys.readouterr().out
    assert main([*args, "history", "PINV-1"]) == 0
    assert "run 1 2026-10-12 00:00 ASK: amount 1000 NGN" in capsys.readouterr().out
    assert main([*args, "answer", "PINV-404", "approve"]) == 1
    assert "has no decision to answer" in capsys.readouterr().err


def test_a_supplier_without_a_challenge_gets_a_clear_message(env, tmp_path, capsys):
    assert main([*no_env_file(tmp_path), "challenge", "S"]) == 1
    assert "no wallet challenge for S" in capsys.readouterr().err
