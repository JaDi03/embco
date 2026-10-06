"""SQL for the SQLite journal: tables, append-only triggers and the statements that use them."""

TABLES = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    policy TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs (id),
    invoice TEXT NOT NULL,
    supplier TEXT NOT NULL,
    amount TEXT NOT NULL,
    due_date TEXT,
    action TEXT NOT NULL,
    reasons TEXT NOT NULL,
    findings TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_by_invoice ON decisions (invoice, id);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    verdict TEXT NOT NULL,
    answered_by TEXT NOT NULL,
    answered_at TEXT NOT NULL,
    note TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS answers_by_invoice ON answers (invoice, id);
"""

APPEND_ONLY = "".join(
    f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
    "BEGIN SELECT RAISE(ABORT, 'the journal is append-only'); END;\n"
    for table in ("runs", "decisions", "answers")
    for op in ("UPDATE", "DELETE")
)

SELECT_DECISIONS = """
SELECT d.run_id, r.started_at, r.policy, d.invoice, d.supplier, d.amount, d.due_date,
       d.action, d.reasons, d.findings, d.fingerprint, d.entry_hash
FROM decisions d JOIN runs r ON r.id = d.run_id
"""

INSERT_DECISION = """
INSERT INTO decisions (run_id, invoice, supplier, amount, due_date, action, reasons, findings,
                       fingerprint, entry_hash)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

DECISION_KEYS = (
    "run_id", "recorded_at", "policy", "invoice", "supplier", "amount", "due_date", "action",
    "reasons", "findings", "fingerprint",
)

SELECT_ANSWERS = """
SELECT invoice, fingerprint, verdict, answered_by, answered_at, note, entry_hash FROM answers
"""

INSERT_ANSWER = """
INSERT INTO answers (invoice, fingerprint, verdict, answered_by, answered_at, note, entry_hash)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

ANSWER_KEYS = ("invoice", "fingerprint", "verdict", "answered_by", "answered_at", "note")
