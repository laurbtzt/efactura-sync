from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from efactura_sync.storage.files import FileStore


def test_atomic_write_creates_parent_dirs(tmp_path: Path) -> None:
    store = FileStore()
    target = tmp_path / "a" / "b" / "c.bin"

    store.atomic_write(target, b"hello")

    assert target.read_bytes() == b"hello"
    assert not (target.with_suffix(".bin.partial")).exists()


def test_atomic_write_overwrites_existing(tmp_path: Path) -> None:
    store = FileStore()
    target = tmp_path / "x.bin"
    target.write_bytes(b"old")

    store.atomic_write(target, b"new")

    assert target.read_bytes() == b"new"


def test_sweep_partials_removes_old_files(tmp_path: Path) -> None:
    store = FileStore()
    fresh = tmp_path / "fresh.bin.partial"
    stale = tmp_path / "stale.bin.partial"
    fresh.write_bytes(b"")
    stale.write_bytes(b"")
    one_hour_ago = datetime.now(UTC) - timedelta(hours=2)
    import os

    os.utime(stale, (one_hour_ago.timestamp(), one_hour_ago.timestamp()))

    removed = store.sweep_partials(tmp_path, older_than=timedelta(hours=1))

    assert removed == [stale]
    assert fresh.exists()
    assert not stale.exists()


def test_sweep_partials_handles_missing_dir(tmp_path: Path) -> None:
    store = FileStore()
    removed = store.sweep_partials(tmp_path / "does-not-exist", older_than=timedelta(hours=1))
    assert removed == []


def test_atomic_write_logs_debug(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    target = tmp_path / "sub" / "x.bin"
    with caplog.at_level("DEBUG", logger="efactura_sync.storage.files"):
        FileStore().atomic_write(target, b"hello")

    assert target.read_bytes() == b"hello"
    assert any("atomic_write" in r.message for r in caplog.records)
