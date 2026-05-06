"""SQLite schema and queries.

The connection itself is owned by callers (so tests can use ``:memory:``).
Every public function takes a ``sqlite3.Connection`` as its first argument.
"""

import sqlite3

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS monitored_cuis (
  cui            TEXT PRIMARY KEY,
  display_name   TEXT,
  added_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracked_counterparties (
  my_cui            TEXT NOT NULL,
  counterparty_cui  TEXT NOT NULL,
  added_at          TEXT NOT NULL,
  PRIMARY KEY (my_cui, counterparty_cui),
  FOREIGN KEY (my_cui) REFERENCES monitored_cuis(cui) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS poll_state (
  cui              TEXT NOT NULL,
  env              TEXT NOT NULL,
  last_polled_at   TEXT NOT NULL,
  PRIMARY KEY (cui, env)
);

CREATE TABLE IF NOT EXISTS synced_messages (
  msg_id              TEXT NOT NULL,
  cui                 TEXT NOT NULL,
  env                 TEXT NOT NULL,
  msg_type            TEXT NOT NULL,
  counterparty_cui    TEXT,
  issue_date          TEXT,
  zip_path            TEXT,
  pdf_path            TEXT,
  email_sent_at       TEXT,
  email_skip_reason   TEXT,
  first_seen_at       TEXT NOT NULL,
  last_attempt_at     TEXT NOT NULL,
  last_error          TEXT,
  PRIMARY KEY (msg_id, cui, env)
);

CREATE INDEX IF NOT EXISTS idx_msg_cui
  ON synced_messages(cui, env);

CREATE INDEX IF NOT EXISTS idx_msg_pending
  ON synced_messages(cui, env)
  WHERE zip_path IS NULL
     OR (msg_type IN ('PRIMITA','TRIMISA') AND pdf_path IS NULL)
     OR (email_sent_at IS NULL AND email_skip_reason IS NULL);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they don't exist. Idempotent."""
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
