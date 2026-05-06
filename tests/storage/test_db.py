import sqlite3
from datetime import datetime

import pytest

from efactura_sync.storage.db import (
    MonitoredCui,
    add_monitored_cui,
    add_tracked_counterparty,
    init_schema,
    is_counterparty_tracked,
    list_monitored_cuis,
    list_tracked_counterparties,
    remove_monitored_cui,
    remove_tracked_counterparty,
)


def test_init_schema_is_idempotent(db: sqlite3.Connection) -> None:
    init_schema(db)
    init_schema(db)  # second call must not raise

    rows = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    names = [r[0] for r in rows]
    assert names == [
        "monitored_cuis",
        "poll_state",
        "synced_messages",
        "tracked_counterparties",
    ]


def test_indexes_exist(db: sqlite3.Connection) -> None:
    init_schema(db)
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"idx_msg_cui", "idx_msg_pending"}.issubset(names)


def test_foreign_key_cascade_drops_tracked_when_monitored_cui_removed(
    db: sqlite3.Connection,
) -> None:
    init_schema(db)
    db.execute(
        "INSERT INTO monitored_cuis(cui, display_name, added_at) VALUES (?,?,?)",
        ("12345678", "Acme", "2026-05-04T10:00:00Z"),
    )
    db.execute(
        "INSERT INTO tracked_counterparties(my_cui, counterparty_cui, added_at) VALUES (?,?,?)",
        ("12345678", "RO111", "2026-05-04T10:00:00Z"),
    )
    db.commit()

    db.execute("DELETE FROM monitored_cuis WHERE cui = ?", ("12345678",))
    db.commit()

    n = db.execute("SELECT count(*) FROM tracked_counterparties").fetchone()[0]
    assert n == 0


def test_add_and_list_monitored_cui(db: sqlite3.Connection, now_utc: datetime) -> None:
    init_schema(db)
    add_monitored_cui(db, cui="12345678", display_name="Acme SRL", now=now_utc)

    cuis = list_monitored_cuis(db)
    assert cuis == [MonitoredCui(cui="12345678", display_name="Acme SRL", added_at=now_utc)]


def test_add_monitored_cui_is_idempotent_on_conflict(
    db: sqlite3.Connection, now_utc: datetime
) -> None:
    init_schema(db)
    add_monitored_cui(db, cui="12345678", display_name="Acme", now=now_utc)
    later = now_utc.replace(year=2027)
    add_monitored_cui(db, cui="12345678", display_name="Acme Updated", now=later)

    [c] = list_monitored_cuis(db)
    assert c.display_name == "Acme Updated"
    assert c.added_at == later  # upsert overwrites


def test_remove_monitored_cui(db: sqlite3.Connection, now_utc: datetime) -> None:
    init_schema(db)
    add_monitored_cui(db, cui="12345678", display_name=None, now=now_utc)
    remove_monitored_cui(db, cui="12345678")
    assert list_monitored_cuis(db) == []


def test_track_add_list_remove(db: sqlite3.Connection, now_utc: datetime) -> None:
    init_schema(db)
    add_monitored_cui(db, cui="12345678", display_name=None, now=now_utc)
    add_tracked_counterparty(db, my_cui="12345678", counterparty_cui="RO111", now=now_utc)
    add_tracked_counterparty(db, my_cui="12345678", counterparty_cui="RO222", now=now_utc)

    assert sorted(list_tracked_counterparties(db, my_cui="12345678")) == ["RO111", "RO222"]
    assert is_counterparty_tracked(db, my_cui="12345678", counterparty_cui="RO111") is True
    assert is_counterparty_tracked(db, my_cui="12345678", counterparty_cui="UNKNOWN") is False

    remove_tracked_counterparty(db, my_cui="12345678", counterparty_cui="RO111")
    assert list_tracked_counterparties(db, my_cui="12345678") == ["RO222"]


def test_add_tracked_counterparty_requires_existing_monitored_cui(
    db: sqlite3.Connection, now_utc: datetime
) -> None:
    init_schema(db)
    with pytest.raises(sqlite3.IntegrityError):  # FK violation
        add_tracked_counterparty(db, my_cui="UNKNOWN", counterparty_cui="RO111", now=now_utc)
