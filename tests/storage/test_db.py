import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

from efactura_sync.storage.db import (
    MonitoredCui,
    PollState,
    SyncedMessage,
    _iso,
    _parse_iso,
    add_monitored_cui,
    add_tracked_counterparty,
    connect,
    find_pending_rows,
    get_poll_state,
    get_synced_message,
    init_schema,
    insert_synced_message,
    is_counterparty_tracked,
    list_monitored_cuis,
    list_tracked_counterparties,
    mark_email_sent,
    mark_email_skipped,
    remove_monitored_cui,
    remove_tracked_counterparty,
    update_attempt,
    update_pdf_path,
    update_zip_path,
    upsert_poll_state,
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


def test_remove_monitored_cui_also_deletes_poll_state(db, now_utc) -> None:
    init_schema(db)
    add_monitored_cui(db, cui="12345678", display_name=None, now=now_utc)
    upsert_poll_state(db, cui="12345678", env="prod", last_polled_at=now_utc)
    upsert_poll_state(db, cui="12345678", env="test", last_polled_at=now_utc)

    remove_monitored_cui(db, cui="12345678")

    assert get_poll_state(db, cui="12345678", env="prod") is None
    assert get_poll_state(db, cui="12345678", env="test") is None


def test_remove_monitored_cui_also_deletes_synced_messages(db, now_utc) -> None:
    init_schema(db)
    add_monitored_cui(db, cui="12345678", display_name=None, now=now_utc)
    insert_synced_message(db, _msg(msg_id="A"))
    insert_synced_message(db, _msg(msg_id="B"))

    remove_monitored_cui(db, cui="12345678")

    assert get_synced_message(db, msg_id="A", cui="12345678", env="prod") is None
    assert get_synced_message(db, msg_id="B", cui="12345678", env="prod") is None


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


def test_iso_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="naive datetime"):
        _iso(datetime(2026, 5, 4, 10, 0, 0))


def test_iso_coerces_non_utc_tz_to_utc() -> None:
    bucharest_summer = timezone(timedelta(hours=3))  # EEST
    local = datetime(2026, 5, 4, 13, 0, 0, tzinfo=bucharest_summer)
    # 13:00 EEST == 10:00 UTC
    assert _iso(local) == "2026-05-04T10:00:00Z"


def test_iso_round_trip_preserves_utc() -> None:
    original = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
    assert _parse_iso(_iso(original)) == original


def test_get_poll_state_missing_returns_none(db: sqlite3.Connection) -> None:
    init_schema(db)
    assert get_poll_state(db, cui="12345678", env="prod") is None


def test_upsert_poll_state_inserts_then_updates(db: sqlite3.Connection, now_utc: datetime) -> None:
    init_schema(db)
    upsert_poll_state(db, cui="12345678", env="prod", last_polled_at=now_utc)

    state = get_poll_state(db, cui="12345678", env="prod")
    assert state == PollState(cui="12345678", env="prod", last_polled_at=now_utc)

    later = now_utc.replace(day=5)
    upsert_poll_state(db, cui="12345678", env="prod", last_polled_at=later)
    state2 = get_poll_state(db, cui="12345678", env="prod")
    assert state2 is not None
    assert state2.last_polled_at == later


def test_poll_state_is_keyed_by_cui_and_env(db: sqlite3.Connection, now_utc: datetime) -> None:
    init_schema(db)
    upsert_poll_state(db, cui="12345678", env="prod", last_polled_at=now_utc)
    upsert_poll_state(db, cui="12345678", env="test", last_polled_at=now_utc)
    assert get_poll_state(db, cui="12345678", env="prod") is not None
    assert get_poll_state(db, cui="12345678", env="test") is not None


def _msg(**overrides: Any) -> SyncedMessage:
    base = SyncedMessage(
        msg_id="3001",
        cui="12345678",
        env="prod",
        msg_type="PRIMITA",
        counterparty_cui="RO111",
        issue_date=date(2026, 5, 4),
        zip_path=None,
        pdf_path=None,
        email_sent_at=None,
        email_skip_reason=None,
        first_seen_at=datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
        last_attempt_at=datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
        last_error=None,
    )
    return SyncedMessage(**{**base.__dict__, **overrides})


def test_insert_synced_message_returns_true_when_new(
    db: sqlite3.Connection, now_utc: datetime
) -> None:
    init_schema(db)
    inserted = insert_synced_message(db, _msg())
    assert inserted is True

    inserted_again = insert_synced_message(db, _msg())
    assert inserted_again is False  # ON CONFLICT DO NOTHING


def test_get_synced_message_round_trip(db: sqlite3.Connection, now_utc: datetime) -> None:
    init_schema(db)
    insert_synced_message(db, _msg())
    got = get_synced_message(db, msg_id="3001", cui="12345678", env="prod")
    assert got is not None
    assert got.msg_type == "PRIMITA"
    assert got.issue_date == date(2026, 5, 4)


def test_step_markers_set_paths_and_email(db: sqlite3.Connection, now_utc: datetime) -> None:
    init_schema(db)
    insert_synced_message(db, _msg())

    update_zip_path(db, msg_id="3001", cui="12345678", env="prod", zip_path="rel/zip", now=now_utc)
    update_pdf_path(db, msg_id="3001", cui="12345678", env="prod", pdf_path="rel/pdf", now=now_utc)
    mark_email_sent(db, msg_id="3001", cui="12345678", env="prod", sent_at=now_utc)

    row = get_synced_message(db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.zip_path == "rel/zip"
    assert row.pdf_path == "rel/pdf"
    assert row.email_sent_at == now_utc
    assert row.last_error is None


def test_mark_email_skipped_sets_reason(db: sqlite3.Connection, now_utc: datetime) -> None:
    init_schema(db)
    insert_synced_message(db, _msg())
    mark_email_skipped(
        db,
        msg_id="3001",
        cui="12345678",
        env="prod",
        reason="filtered_by_track_list",
        now=now_utc,
    )
    row = get_synced_message(db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.email_skip_reason == "filtered_by_track_list"
    assert row.email_sent_at is None


def test_update_attempt_records_error_and_clears_on_success(
    db: sqlite3.Connection, now_utc: datetime
) -> None:
    init_schema(db)
    insert_synced_message(db, _msg())

    update_attempt(db, msg_id="3001", cui="12345678", env="prod", error="boom", now=now_utc)
    row1 = get_synced_message(db, msg_id="3001", cui="12345678", env="prod")
    assert row1 is not None
    assert row1.last_error == "boom"

    update_attempt(db, msg_id="3001", cui="12345678", env="prod", error=None, now=now_utc)
    row2 = get_synced_message(db, msg_id="3001", cui="12345678", env="prod")
    assert row2 is not None
    assert row2.last_error is None


def test_find_pending_rows_returns_only_unfinished(
    db: sqlite3.Connection, now_utc: datetime
) -> None:
    init_schema(db)
    # complete row: zip + pdf + email_sent_at
    insert_synced_message(db, _msg(msg_id="A"))
    update_zip_path(db, msg_id="A", cui="12345678", env="prod", zip_path="zA", now=now_utc)
    update_pdf_path(db, msg_id="A", cui="12345678", env="prod", pdf_path="pA", now=now_utc)
    mark_email_sent(db, msg_id="A", cui="12345678", env="prod", sent_at=now_utc)

    # PRIMITA missing pdf
    insert_synced_message(db, _msg(msg_id="B"))
    update_zip_path(db, msg_id="B", cui="12345678", env="prod", zip_path="zB", now=now_utc)

    # MESAJ — no pdf needed; missing email
    insert_synced_message(
        db,
        _msg(msg_id="C", msg_type="MESAJ", counterparty_cui=None, issue_date=None),
    )
    update_zip_path(db, msg_id="C", cui="12345678", env="prod", zip_path="zC", now=now_utc)

    pending = {r.msg_id for r in find_pending_rows(db, cui="12345678", env="prod")}
    assert pending == {"B", "C"}


def test_init_schema_sets_foreign_keys_pragma_on_fresh_connection(tmp_path) -> None:
    """`init_schema` must turn FK enforcement on for the connection it receives."""
    db_path = tmp_path / "scratch.db"
    # Use raw sqlite3.connect (NOT our connect() helper) to prove init_schema is
    # the one turning on the pragma, independent of the helper.
    raw = sqlite3.connect(db_path)
    init_schema(raw)
    [(fk_on,)] = raw.execute("PRAGMA foreign_keys").fetchall()
    assert fk_on == 1
    raw.close()


def test_connect_helper_sets_foreign_keys(tmp_path) -> None:
    """Our connect() helper must enable foreign keys."""
    db_path = tmp_path / "scratch2.db"
    conn = connect(db_path)
    [(fk_on,)] = conn.execute("PRAGMA foreign_keys").fetchall()
    assert fk_on == 1
    conn.close()


def test_remove_tracked_counterparty_does_not_touch_monitored_cui(db, now_utc) -> None:
    """Cascade is one-way: deleting a tracked row must not touch the parent."""
    init_schema(db)
    add_monitored_cui(db, cui="12345678", display_name="Acme", now=now_utc)
    add_tracked_counterparty(db, my_cui="12345678", counterparty_cui="RO111", now=now_utc)

    remove_tracked_counterparty(db, my_cui="12345678", counterparty_cui="RO111")

    # Monitored CUI still present.
    cuis = list_monitored_cuis(db)
    assert len(cuis) == 1
    assert cuis[0].cui == "12345678"


def test_monitored_cui_added_at_not_null(db) -> None:
    """`added_at` is NOT NULL — proves the schema constraint is alive."""
    init_schema(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO monitored_cuis(cui, display_name, added_at) VALUES (?,?,?)",
            ("12345678", "Acme", None),
        )
