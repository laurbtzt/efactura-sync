import sqlite3

from efactura_sync.storage.db import init_schema


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
