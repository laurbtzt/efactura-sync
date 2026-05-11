"""Atomic file writes and stale `.partial` sweep."""

import os
import time
from datetime import timedelta
from pathlib import Path


class FileStore:
    """Filesystem operations behind a small interface so tests can swap it."""

    def atomic_write(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(partial, target)
        # fsync the parent directory so the rename itself is durable on
        # power loss — without this, the file contents survive but the
        # directory entry can be lost. Best-effort: some filesystems
        # (e.g. on Windows) raise PermissionError on O_DIRECTORY.
        try:
            dir_fd = os.open(target.parent, os.O_DIRECTORY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def sweep_partials(self, root: Path, *, older_than: timedelta) -> list[Path]:
        if not root.exists():
            return []
        cutoff = time.time() - older_than.total_seconds()
        removed: list[Path] = []
        for partial in root.rglob("*.partial"):
            if partial.is_file() and partial.stat().st_mtime < cutoff:
                partial.unlink()
                removed.append(partial)
        return removed
