"""SQLite schema and queries.

The connection itself is owned by callers (so tests can use ``:memory:``).
Every public function takes a ``sqlite3.Connection`` as its first argument.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

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


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime not allowed; pass UTC-aware")
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


@dataclass(frozen=True)
class MonitoredCui:
    cui: str
    display_name: str | None
    added_at: datetime


def add_monitored_cui(
    conn: sqlite3.Connection,
    *,
    cui: str,
    display_name: str | None,
    now: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO monitored_cuis(cui, display_name, added_at)
        VALUES (?, ?, ?)
        ON CONFLICT(cui) DO UPDATE SET
            display_name = excluded.display_name,
            added_at     = excluded.added_at
        """,
        (cui, display_name, _iso(now)),
    )
    conn.commit()


def list_monitored_cuis(conn: sqlite3.Connection) -> list[MonitoredCui]:
    rows = conn.execute(
        "SELECT cui, display_name, added_at FROM monitored_cuis ORDER BY cui"
    ).fetchall()
    return [MonitoredCui(cui=r[0], display_name=r[1], added_at=_parse_iso(r[2])) for r in rows]


def remove_monitored_cui(conn: sqlite3.Connection, *, cui: str) -> None:
    """Remove a monitored CUI and all rows that reference it.

    `tracked_counterparties` cascades automatically via FK; `poll_state`
    and `synced_messages` have no FK so we delete them explicitly.
    """
    conn.execute("DELETE FROM synced_messages WHERE cui = ?", (cui,))
    conn.execute("DELETE FROM poll_state WHERE cui = ?", (cui,))
    conn.execute("DELETE FROM monitored_cuis WHERE cui = ?", (cui,))
    conn.commit()


def add_tracked_counterparty(
    conn: sqlite3.Connection,
    *,
    my_cui: str,
    counterparty_cui: str,
    now: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO tracked_counterparties(my_cui, counterparty_cui, added_at)
        VALUES (?, ?, ?)
        ON CONFLICT(my_cui, counterparty_cui) DO NOTHING
        """,
        (my_cui, counterparty_cui, _iso(now)),
    )
    conn.commit()


def list_tracked_counterparties(conn: sqlite3.Connection, *, my_cui: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT counterparty_cui
        FROM tracked_counterparties
        WHERE my_cui = ?
        ORDER BY counterparty_cui
        """,
        (my_cui,),
    ).fetchall()
    return [r[0] for r in rows]


def is_counterparty_tracked(
    conn: sqlite3.Connection, *, my_cui: str, counterparty_cui: str
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM tracked_counterparties WHERE my_cui = ? AND counterparty_cui = ?",
        (my_cui, counterparty_cui),
    ).fetchone()
    return row is not None


def remove_tracked_counterparty(
    conn: sqlite3.Connection, *, my_cui: str, counterparty_cui: str
) -> None:
    conn.execute(
        "DELETE FROM tracked_counterparties WHERE my_cui = ? AND counterparty_cui = ?",
        (my_cui, counterparty_cui),
    )
    conn.commit()


@dataclass(frozen=True)
class PollState:
    cui: str
    env: str
    last_polled_at: datetime


def get_poll_state(conn: sqlite3.Connection, *, cui: str, env: str) -> PollState | None:
    row = conn.execute(
        "SELECT cui, env, last_polled_at FROM poll_state WHERE cui = ? AND env = ?",
        (cui, env),
    ).fetchone()
    if row is None:
        return None
    return PollState(cui=row[0], env=row[1], last_polled_at=_parse_iso(row[2]))


def upsert_poll_state(
    conn: sqlite3.Connection, *, cui: str, env: str, last_polled_at: datetime
) -> None:
    conn.execute(
        """
        INSERT INTO poll_state(cui, env, last_polled_at) VALUES (?, ?, ?)
        ON CONFLICT(cui, env) DO UPDATE SET last_polled_at = excluded.last_polled_at
        """,
        (cui, env, _iso(last_polled_at)),
    )
    conn.commit()


@dataclass(frozen=True)
class SyncedMessage:
    msg_id: str
    cui: str
    env: str
    msg_type: str  # 'PRIMITA' | 'TRIMISA' | 'ERORI' | 'MESAJ'
    counterparty_cui: str | None
    issue_date: date | None
    zip_path: str | None
    pdf_path: str | None
    email_sent_at: datetime | None
    email_skip_reason: str | None
    first_seen_at: datetime
    last_attempt_at: datetime
    last_error: str | None


def _row_to_msg(row: tuple[Any, ...]) -> SyncedMessage:
    return SyncedMessage(
        msg_id=row[0],
        cui=row[1],
        env=row[2],
        msg_type=row[3],
        counterparty_cui=row[4],
        issue_date=date.fromisoformat(row[5]) if row[5] else None,
        zip_path=row[6],
        pdf_path=row[7],
        email_sent_at=_parse_iso(row[8]) if row[8] else None,
        email_skip_reason=row[9],
        first_seen_at=_parse_iso(row[10]),
        last_attempt_at=_parse_iso(row[11]),
        last_error=row[12],
    )


_SELECT_COLS = (
    "msg_id, cui, env, msg_type, counterparty_cui, issue_date, "
    "zip_path, pdf_path, email_sent_at, email_skip_reason, "
    "first_seen_at, last_attempt_at, last_error"
)


def insert_synced_message(conn: sqlite3.Connection, msg: SyncedMessage) -> bool:
    cur = conn.execute(
        f"""
        INSERT INTO synced_messages({_SELECT_COLS})
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(msg_id, cui, env) DO NOTHING
        """,
        (
            msg.msg_id,
            msg.cui,
            msg.env,
            msg.msg_type,
            msg.counterparty_cui,
            msg.issue_date.isoformat() if msg.issue_date else None,
            msg.zip_path,
            msg.pdf_path,
            _iso(msg.email_sent_at) if msg.email_sent_at else None,
            msg.email_skip_reason,
            _iso(msg.first_seen_at),
            _iso(msg.last_attempt_at),
            msg.last_error,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def get_synced_message(
    conn: sqlite3.Connection, *, msg_id: str, cui: str, env: str
) -> SyncedMessage | None:
    row = conn.execute(
        f"SELECT {_SELECT_COLS} FROM synced_messages WHERE msg_id=? AND cui=? AND env=?",
        (msg_id, cui, env),
    ).fetchone()
    return None if row is None else _row_to_msg(row)


def update_zip_path(
    conn: sqlite3.Connection,
    *,
    msg_id: str,
    cui: str,
    env: str,
    zip_path: str,
    now: datetime,
) -> None:
    conn.execute(
        "UPDATE synced_messages SET zip_path=?, last_attempt_at=?, last_error=NULL "
        "WHERE msg_id=? AND cui=? AND env=?",
        (zip_path, _iso(now), msg_id, cui, env),
    )
    conn.commit()


def update_pdf_path(
    conn: sqlite3.Connection,
    *,
    msg_id: str,
    cui: str,
    env: str,
    pdf_path: str,
    now: datetime,
) -> None:
    conn.execute(
        "UPDATE synced_messages SET pdf_path=?, last_attempt_at=?, last_error=NULL "
        "WHERE msg_id=? AND cui=? AND env=?",
        (pdf_path, _iso(now), msg_id, cui, env),
    )
    conn.commit()


def mark_email_sent(
    conn: sqlite3.Connection, *, msg_id: str, cui: str, env: str, sent_at: datetime
) -> None:
    conn.execute(
        "UPDATE synced_messages SET email_sent_at=?, last_attempt_at=?, last_error=NULL "
        "WHERE msg_id=? AND cui=? AND env=?",
        (_iso(sent_at), _iso(sent_at), msg_id, cui, env),
    )
    conn.commit()


def mark_email_skipped(
    conn: sqlite3.Connection,
    *,
    msg_id: str,
    cui: str,
    env: str,
    reason: str,
    now: datetime,
) -> None:
    conn.execute(
        "UPDATE synced_messages SET email_skip_reason=?, last_attempt_at=?, last_error=NULL "
        "WHERE msg_id=? AND cui=? AND env=?",
        (reason, _iso(now), msg_id, cui, env),
    )
    conn.commit()


def update_attempt(
    conn: sqlite3.Connection,
    *,
    msg_id: str,
    cui: str,
    env: str,
    error: str | None,
    now: datetime,
) -> None:
    conn.execute(
        "UPDATE synced_messages SET last_attempt_at=?, last_error=? "
        "WHERE msg_id=? AND cui=? AND env=?",
        (_iso(now), error, msg_id, cui, env),
    )
    conn.commit()


def find_pending_rows(conn: sqlite3.Connection, *, cui: str, env: str) -> list[SyncedMessage]:
    rows = conn.execute(
        f"""
        SELECT {_SELECT_COLS} FROM synced_messages
        WHERE cui=? AND env=?
          AND (
            zip_path IS NULL
            OR (msg_type IN ('PRIMITA','TRIMISA') AND pdf_path IS NULL)
            OR (email_sent_at IS NULL AND email_skip_reason IS NULL)
          )
        ORDER BY first_seen_at
        """,
        (cui, env),
    ).fetchall()
    return [_row_to_msg(r) for r in rows]
