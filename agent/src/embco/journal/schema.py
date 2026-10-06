"""SQL for the SQLite journal.

One table, one hash chain, in the order things happened. Kinds of entry:
POLICY when the policy changes, RUN for every run, DECISION when a decision is new or changed,
CLOSED when an invoice leaves the unpaid list, ANSWER when the owner answers an ASK, CHALLENGE
when the agent asks a supplier to sign for a wallet, PROOF when a valid signature comes back.
"""

VERSION = 1

TABLES = f"""
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    run INTEGER,
    invoice TEXT,
    at TEXT NOT NULL,
    body TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_by_invoice ON entries (invoice, kind, id);
CREATE INDEX IF NOT EXISTS entries_by_kind ON entries (kind, id);
CREATE TRIGGER IF NOT EXISTS entries_no_update BEFORE UPDATE ON entries
BEGIN SELECT RAISE(ABORT, 'the journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS entries_no_delete BEFORE DELETE ON entries
BEGIN SELECT RAISE(ABORT, 'the journal is append-only'); END;
PRAGMA user_version = {VERSION};
"""

INSERT = "INSERT INTO entries (kind, run, invoice, at, body, entry_hash) VALUES (?, ?, ?, ?, ?, ?)"

ALL = "SELECT kind, run, invoice, at, body, entry_hash FROM entries ORDER BY id"

HEAD = "SELECT entry_hash FROM entries ORDER BY id DESC LIMIT 1"

LAST_RUN = "SELECT MAX(run) FROM entries WHERE kind = 'RUN'"

LAST_POLICY = "SELECT body FROM entries WHERE kind = 'POLICY' ORDER BY id DESC LIMIT 1"

LAST_OF_KIND = """
SELECT run, invoice, at, body, entry_hash FROM entries
WHERE invoice = ? AND kind = ? ORDER BY id DESC LIMIT 1
"""

DECISIONS_OF = """
SELECT run, invoice, at, body, entry_hash FROM entries
WHERE invoice = ? AND kind = 'DECISION' ORDER BY id
"""

LAST_FOR_SUPPLIER = """
SELECT at, body FROM entries
WHERE kind = ? AND json_extract(body, '$.supplier') = ? ORDER BY id DESC LIMIT 1
"""

OPEN_INVOICES = """
SELECT e.invoice FROM entries e
WHERE e.kind = 'DECISION'
  AND e.id = (SELECT MAX(id) FROM entries
              WHERE invoice = e.invoice AND kind IN ('DECISION', 'CLOSED'))
"""
