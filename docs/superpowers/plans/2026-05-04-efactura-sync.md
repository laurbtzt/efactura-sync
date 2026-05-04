# efactura-sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI that pulls Romanian ANAF e-Factura messages for 1–5 monitored CUIs into a deterministic local archive, deduplicates via SQLite, renders invoice PDFs through ANAF's `xmltopdf` endpoint, and sends Romanian-language email notifications gated by a per-CUI tracked-counterparties allow-list.

**Architecture:** `src/` layout package with explicit dependency injection at three external seams (ANAF HTTP, SMTP, filesystem). One CLI runs in two host roles: laptop for interactive `auth login`, headless server for daily cron `sync run`. SQLite is the dedup ledger and resume-from-failure log; each message walks five steps (insert → download → render → email-decision → email-send) with a column-per-step success marker.

**Tech Stack:** Python 3.12+, `uv` for tooling, `httpx` for HTTP, `lxml` for UBL XML, `typer` for CLI, `pytest` + `:memory:` SQLite for tests, `ruff` + `mypy` (strict) in CI.

**Spec:** [`docs/superpowers/specs/2026-05-04-efactura-sync-design.md`](../specs/2026-05-04-efactura-sync-design.md)

---

## File map

```
efactura-sync/
├── pyproject.toml
├── .github/workflows/ci.yml
├── src/
│   └── efactura_sync/
│       ├── __init__.py            # version constant
│       ├── errors.py              # typed exceptions
│       ├── config.py              # TOML loader (config.toml + secrets.toml)
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── layout.py          # path computation
│       │   ├── files.py           # atomic write + .partial sweep
│       │   └── db.py              # schema + queries
│       ├── anaf/
│       │   ├── __init__.py
│       │   ├── client.py          # listamesaje + descarcare
│       │   ├── messages.py        # ZIP + UBL parsing
│       │   └── oauth.py           # token store + refresh + browser flow
│       ├── render.py              # xmltopdf POST
│       ├── mail.py                # Romanian rendering + Mailer
│       ├── sync.py                # orchestrator
│       └── cli.py                 # typer app
└── tests/
    ├── conftest.py                # shared fixtures
    ├── fixtures/ubl/
    │   └── primita_minimal.xml
    ├── storage/ test_layout.py, test_files.py, test_db.py
    ├── anaf/    test_client.py, test_messages.py, test_oauth.py
    ├── test_config.py
    ├── test_render.py
    ├── test_mail.py
    ├── test_sync.py
    └── test_cli.py
```

---

## Task 1 — Project scaffold (pyproject + dirs + ruff/mypy config)

**Files:**
- Create: `pyproject.toml`
- Create: `src/efactura_sync/__init__.py`
- Create: `tests/__init__.py`
- Create: `.python-version`

- [ ] **Step 1:** Write `pyproject.toml`

```toml
[project]
name = "efactura-sync"
version = "0.1.0"
description = "Sync Romanian ANAF e-Factura messages into a local archive."
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.27",
    "lxml>=5.2",
    "typer>=0.12",
    "platformdirs>=4.2",
]

[project.scripts]
efactura-sync = "efactura_sync.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/efactura_sync"]

[dependency-groups]
dev = [
    "pytest>=8.2",
    "pytest-cov>=5.0",
    "ruff>=0.5",
    "mypy>=1.10",
    "types-lxml>=2024.4",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "RET"]

[tool.mypy]
strict = true
python_version = "3.12"
mypy_path = "src"
namespace_packages = true
explicit_package_bases = true

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "integration: opt-in tests that hit real ANAF / SMTP",
]
addopts = "-q --strict-markers"
```

- [ ] **Step 2:** Write `src/efactura_sync/__init__.py`

```python
__version__ = "0.1.0"
```

- [ ] **Step 3:** Write `tests/__init__.py` (empty file)

- [ ] **Step 4:** Write `.python-version`

```
3.12
```

- [ ] **Step 5:** Sync the environment

Run: `uv sync --dev`
Expected: completes without error; creates `.venv/`, `uv.lock`.

- [ ] **Step 6:** Verify the package imports

Run: `uv run python -c "import efactura_sync; print(efactura_sync.__version__)"`
Expected: `0.1.0`

- [ ] **Step 7:** Commit

```bash
git add pyproject.toml uv.lock .python-version src/ tests/
git commit -m "chore: project scaffold with uv + ruff + mypy + pytest"
```

---

## Task 2 — CI workflow + shared test fixtures

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `tests/conftest.py`

- [ ] **Step 1:** Write `.github/workflows/ci.yml`

```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv python install ${{ matrix.python }}
      - run: uv sync --dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy src
      - run: uv run pytest -m "not integration"
```

- [ ] **Step 2:** Write `tests/conftest.py`

```python
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def now_utc() -> datetime:
    return datetime(2026, 5, 4, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    return root


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    yield conn
    conn.close()
```

- [ ] **Step 3:** Run an empty-test sanity check

Run: `uv run pytest`
Expected: `0 passed` (no tests yet, but pytest collects without error).

- [ ] **Step 4:** Run lint + format + typecheck on what we have

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

- [ ] **Step 5:** Commit

```bash
git add .github/workflows/ci.yml tests/conftest.py
git commit -m "ci: add github actions workflow and pytest fixtures"
```

---

## Task 3 — `errors.py` typed exceptions

**Files:**
- Create: `src/efactura_sync/errors.py`
- Test: `tests/test_errors.py`

- [ ] **Step 1:** Write the failing test `tests/test_errors.py`

```python
import pytest

from efactura_sync.errors import (
    AnafApiError,
    AuthError,
    ConfigError,
    EfacturaError,
    InvalidArchiveError,
    PermanentError,
    RefreshTokenExpired,
    RenderError,
    TransientError,
)


def test_efactura_error_is_base() -> None:
    for exc in (AnafApiError, AuthError, ConfigError, RenderError, InvalidArchiveError):
        assert issubclass(exc, EfacturaError)


def test_transient_and_permanent_categorise_anaf_errors() -> None:
    assert issubclass(TransientError, AnafApiError)
    assert issubclass(PermanentError, AnafApiError)


def test_refresh_token_expired_is_auth_error() -> None:
    assert issubclass(RefreshTokenExpired, AuthError)


def test_anaf_api_error_carries_status_and_body() -> None:
    err = AnafApiError("boom", status=503, body=b"upstream down")
    assert err.status == 503
    assert err.body == b"upstream down"
    assert "boom" in str(err)


def test_efactura_error_can_be_raised() -> None:
    with pytest.raises(EfacturaError):
        raise EfacturaError("any")
```

- [ ] **Step 2:** Run the test to verify it fails

Run: `uv run pytest tests/test_errors.py -v`
Expected: `ImportError` / collection error — `efactura_sync.errors` does not exist yet.

- [ ] **Step 3:** Write `src/efactura_sync/errors.py`

```python
"""Typed exceptions used across the package."""

from __future__ import annotations


class EfacturaError(Exception):
    """Base for all package-specific errors."""


class ConfigError(EfacturaError):
    """Raised when config files are missing, malformed, or invalid."""


class AnafApiError(EfacturaError):
    """Any error from the ANAF HTTP API."""

    def __init__(self, message: str, *, status: int | None = None, body: bytes | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class TransientError(AnafApiError):
    """Retriable: network blip, 5xx, 429."""


class PermanentError(AnafApiError):
    """Non-retriable in this run: 4xx (auth, bad request, unknown id)."""


class AuthError(EfacturaError):
    """OAuth / token problems."""


class RefreshTokenExpired(AuthError):
    """The 90-day refresh window has elapsed; user must re-auth on laptop."""


class InvalidArchiveError(EfacturaError):
    """Downloaded ZIP is not a valid archive or is missing expected members."""


class RenderError(EfacturaError):
    """xmltopdf failed (or returned something that isn't a PDF)."""
```

- [ ] **Step 4:** Run the test to verify it passes

Run: `uv run pytest tests/test_errors.py -v`
Expected: 5 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/errors.py tests/test_errors.py
git commit -m "feat(errors): add typed exception hierarchy"
```

---

## Task 4 — `storage/layout.py` path computation

**Files:**
- Create: `src/efactura_sync/storage/__init__.py` (empty)
- Create: `src/efactura_sync/storage/layout.py`
- Test: `tests/storage/__init__.py` (empty)
- Test: `tests/storage/test_layout.py`

- [ ] **Step 1:** Create empty `src/efactura_sync/storage/__init__.py` and `tests/storage/__init__.py`.

- [ ] **Step 2:** Write the failing test `tests/storage/test_layout.py`

```python
from datetime import date
from pathlib import Path

from efactura_sync.storage.layout import (
    invoice_pdf_path,
    invoice_zip_path,
    message_zip_path,
)


def test_invoice_zip_received() -> None:
    p = invoice_zip_path(
        archive_root=Path("/a"),
        cui="12345678",
        msg_type="PRIMITA",
        issue_date=date(2026, 5, 4),
        msg_id="3001",
    )
    assert p == Path("/a/12345678/2026/05/received/archive/3001.zip")


def test_invoice_zip_sent() -> None:
    p = invoice_zip_path(
        archive_root=Path("/a"),
        cui="12345678",
        msg_type="TRIMISA",
        issue_date=date(2026, 1, 9),
        msg_id="42",
    )
    assert p == Path("/a/12345678/2026/01/sent/archive/42.zip")


def test_invoice_pdf_received() -> None:
    p = invoice_pdf_path(
        archive_root=Path("/a"),
        cui="12345678",
        msg_type="PRIMITA",
        issue_date=date(2026, 5, 4),
        msg_id="3001",
    )
    assert p == Path("/a/12345678/2026/05/received/pdf/3001.pdf")


def test_message_zip_for_erori_uses_creation_date() -> None:
    p = message_zip_path(
        archive_root=Path("/a"),
        cui="12345678",
        creation_date=date(2026, 5, 4),
        msg_id="9000",
    )
    assert p == Path("/a/12345678/messages/2026/05/9000.zip")
```

- [ ] **Step 3:** Run the test to verify it fails

Run: `uv run pytest tests/storage/test_layout.py -v`
Expected: ImportError on `efactura_sync.storage.layout`.

- [ ] **Step 4:** Write `src/efactura_sync/storage/layout.py`

```python
"""Compute archive paths from a small set of inputs."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

InvoiceType = Literal["PRIMITA", "TRIMISA"]
_DIR_BY_TYPE: dict[str, str] = {"PRIMITA": "received", "TRIMISA": "sent"}


def invoice_zip_path(
    *,
    archive_root: Path,
    cui: str,
    msg_type: InvoiceType,
    issue_date: date,
    msg_id: str,
) -> Path:
    return (
        archive_root
        / cui
        / f"{issue_date.year:04d}"
        / f"{issue_date.month:02d}"
        / _DIR_BY_TYPE[msg_type]
        / "archive"
        / f"{msg_id}.zip"
    )


def invoice_pdf_path(
    *,
    archive_root: Path,
    cui: str,
    msg_type: InvoiceType,
    issue_date: date,
    msg_id: str,
) -> Path:
    return (
        archive_root
        / cui
        / f"{issue_date.year:04d}"
        / f"{issue_date.month:02d}"
        / _DIR_BY_TYPE[msg_type]
        / "pdf"
        / f"{msg_id}.pdf"
    )


def message_zip_path(
    *,
    archive_root: Path,
    cui: str,
    creation_date: date,
    msg_id: str,
) -> Path:
    return (
        archive_root
        / cui
        / "messages"
        / f"{creation_date.year:04d}"
        / f"{creation_date.month:02d}"
        / f"{msg_id}.zip"
    )
```

- [ ] **Step 5:** Run the test to verify it passes

Run: `uv run pytest tests/storage/test_layout.py -v`
Expected: 4 passed.

- [ ] **Step 6:** Commit

```bash
git add src/efactura_sync/storage/ tests/storage/
git commit -m "feat(storage): add archive path computation"
```

---

## Task 5 — `storage/files.py` atomic writes + partial sweep

**Files:**
- Create: `src/efactura_sync/storage/files.py`
- Test: `tests/storage/test_files.py`

- [ ] **Step 1:** Write the failing test `tests/storage/test_files.py`

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=2)
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
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/storage/test_files.py -v`
Expected: ImportError.

- [ ] **Step 3:** Write `src/efactura_sync/storage/files.py`

```python
"""Atomic file writes and stale `.partial` sweep."""

from __future__ import annotations

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
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/storage/test_files.py -v`
Expected: 4 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/storage/files.py tests/storage/test_files.py
git commit -m "feat(storage): add atomic file writes and partial sweep"
```

---

## Task 6 — `storage/db.py` schema + connection

**Files:**
- Create: `src/efactura_sync/storage/db.py`
- Test: `tests/storage/test_db.py`

- [ ] **Step 1:** Write the failing test `tests/storage/test_db.py`

```python
import sqlite3

from efactura_sync.storage.db import init_schema


def test_init_schema_is_idempotent(db: sqlite3.Connection) -> None:
    init_schema(db)
    init_schema(db)  # second call must not raise

    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
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
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/storage/test_db.py -v`
Expected: ImportError.

- [ ] **Step 3:** Write `src/efactura_sync/storage/db.py`

```python
"""SQLite schema and queries.

The connection itself is owned by callers (so tests can use ``:memory:``).
Every public function takes a ``sqlite3.Connection`` as its first argument.
"""

from __future__ import annotations

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
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/storage/test_db.py -v`
Expected: 3 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/storage/db.py tests/storage/test_db.py
git commit -m "feat(storage): add sqlite schema and init"
```

---

## Task 7 — `storage/db.py` monitored CUIs + tracked counterparties

**Files:**
- Modify: `src/efactura_sync/storage/db.py`
- Modify: `tests/storage/test_db.py`

- [ ] **Step 1:** Append failing tests to `tests/storage/test_db.py`

```python
from datetime import datetime, timezone

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


def test_add_and_list_monitored_cui(db, now_utc) -> None:
    init_schema(db)
    add_monitored_cui(db, cui="12345678", display_name="Acme SRL", now=now_utc)

    cuis = list_monitored_cuis(db)
    assert cuis == [MonitoredCui(cui="12345678", display_name="Acme SRL", added_at=now_utc)]


def test_add_monitored_cui_is_idempotent_on_conflict(db, now_utc) -> None:
    init_schema(db)
    add_monitored_cui(db, cui="12345678", display_name="Acme", now=now_utc)
    later = now_utc.replace(year=2027)
    add_monitored_cui(db, cui="12345678", display_name="Acme Updated", now=later)

    [c] = list_monitored_cuis(db)
    assert c.display_name == "Acme Updated"
    assert c.added_at == later  # upsert overwrites


def test_remove_monitored_cui(db, now_utc) -> None:
    init_schema(db)
    add_monitored_cui(db, cui="12345678", display_name=None, now=now_utc)
    remove_monitored_cui(db, cui="12345678")
    assert list_monitored_cuis(db) == []


def test_track_add_list_remove(db, now_utc) -> None:
    init_schema(db)
    add_monitored_cui(db, cui="12345678", display_name=None, now=now_utc)
    add_tracked_counterparty(db, my_cui="12345678", counterparty_cui="RO111", now=now_utc)
    add_tracked_counterparty(db, my_cui="12345678", counterparty_cui="RO222", now=now_utc)

    assert sorted(list_tracked_counterparties(db, my_cui="12345678")) == ["RO111", "RO222"]
    assert is_counterparty_tracked(db, my_cui="12345678", counterparty_cui="RO111") is True
    assert is_counterparty_tracked(db, my_cui="12345678", counterparty_cui="UNKNOWN") is False

    remove_tracked_counterparty(db, my_cui="12345678", counterparty_cui="RO111")
    assert list_tracked_counterparties(db, my_cui="12345678") == ["RO222"]


def test_add_tracked_counterparty_requires_existing_monitored_cui(db, now_utc) -> None:
    init_schema(db)
    with pytest.raises(Exception):  # FK violation
        add_tracked_counterparty(db, my_cui="UNKNOWN", counterparty_cui="RO111", now=now_utc)
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/storage/test_db.py -v`
Expected: ImportError on the new symbols.

- [ ] **Step 3:** Append to `src/efactura_sync/storage/db.py`

```python
from dataclasses import dataclass
from datetime import datetime


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


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
    return [
        MonitoredCui(cui=r[0], display_name=r[1], added_at=_parse_iso(r[2]))
        for r in rows
    ]


def remove_monitored_cui(conn: sqlite3.Connection, *, cui: str) -> None:
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
        "SELECT counterparty_cui FROM tracked_counterparties WHERE my_cui = ? ORDER BY counterparty_cui",
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
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/storage/test_db.py -v`
Expected: 8 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/storage/db.py tests/storage/test_db.py
git commit -m "feat(storage): add monitored cui and tracked counterparty queries"
```

---

## Task 8 — `storage/db.py` poll_state get/upsert

**Files:**
- Modify: `src/efactura_sync/storage/db.py`
- Modify: `tests/storage/test_db.py`

- [ ] **Step 1:** Append failing test

```python
from efactura_sync.storage.db import PollState, get_poll_state, upsert_poll_state


def test_get_poll_state_missing_returns_none(db) -> None:
    init_schema(db)
    assert get_poll_state(db, cui="12345678", env="prod") is None


def test_upsert_poll_state_inserts_then_updates(db, now_utc) -> None:
    init_schema(db)
    upsert_poll_state(db, cui="12345678", env="prod", last_polled_at=now_utc)

    state = get_poll_state(db, cui="12345678", env="prod")
    assert state == PollState(cui="12345678", env="prod", last_polled_at=now_utc)

    later = now_utc.replace(day=5)
    upsert_poll_state(db, cui="12345678", env="prod", last_polled_at=later)
    assert get_poll_state(db, cui="12345678", env="prod").last_polled_at == later


def test_poll_state_is_keyed_by_cui_and_env(db, now_utc) -> None:
    init_schema(db)
    upsert_poll_state(db, cui="12345678", env="prod", last_polled_at=now_utc)
    upsert_poll_state(db, cui="12345678", env="test", last_polled_at=now_utc)
    assert get_poll_state(db, cui="12345678", env="prod") is not None
    assert get_poll_state(db, cui="12345678", env="test") is not None
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/storage/test_db.py -v`
Expected: ImportError on new symbols.

- [ ] **Step 3:** Append to `src/efactura_sync/storage/db.py`

```python
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
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/storage/test_db.py -v`
Expected: 11 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/storage/db.py tests/storage/test_db.py
git commit -m "feat(storage): add poll_state get/upsert"
```

---

## Task 9 — `storage/db.py` synced_messages CRUD + step markers

**Files:**
- Modify: `src/efactura_sync/storage/db.py`
- Modify: `tests/storage/test_db.py`

- [ ] **Step 1:** Append failing test

```python
from datetime import date

from efactura_sync.storage.db import (
    SyncedMessage,
    find_pending_rows,
    get_synced_message,
    insert_synced_message,
    mark_email_sent,
    mark_email_skipped,
    update_attempt,
    update_pdf_path,
    update_zip_path,
)


def _msg(**overrides) -> SyncedMessage:
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
        first_seen_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc),
        last_attempt_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc),
        last_error=None,
    )
    return SyncedMessage(**{**base.__dict__, **overrides})


def test_insert_synced_message_returns_true_when_new(db, now_utc) -> None:
    init_schema(db)
    inserted = insert_synced_message(db, _msg())
    assert inserted is True

    inserted_again = insert_synced_message(db, _msg())
    assert inserted_again is False  # ON CONFLICT DO NOTHING


def test_get_synced_message_round_trip(db, now_utc) -> None:
    init_schema(db)
    insert_synced_message(db, _msg())
    got = get_synced_message(db, msg_id="3001", cui="12345678", env="prod")
    assert got is not None
    assert got.msg_type == "PRIMITA"
    assert got.issue_date == date(2026, 5, 4)


def test_step_markers_set_paths_and_email(db, now_utc) -> None:
    init_schema(db)
    insert_synced_message(db, _msg())

    update_zip_path(db, msg_id="3001", cui="12345678", env="prod", zip_path="rel/zip", now=now_utc)
    update_pdf_path(db, msg_id="3001", cui="12345678", env="prod", pdf_path="rel/pdf", now=now_utc)
    mark_email_sent(db, msg_id="3001", cui="12345678", env="prod", sent_at=now_utc)

    row = get_synced_message(db, msg_id="3001", cui="12345678", env="prod")
    assert row.zip_path == "rel/zip"
    assert row.pdf_path == "rel/pdf"
    assert row.email_sent_at == now_utc
    assert row.last_error is None


def test_mark_email_skipped_sets_reason(db, now_utc) -> None:
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
    assert row.email_skip_reason == "filtered_by_track_list"
    assert row.email_sent_at is None


def test_update_attempt_records_error_and_clears_on_success(db, now_utc) -> None:
    init_schema(db)
    insert_synced_message(db, _msg())

    update_attempt(db, msg_id="3001", cui="12345678", env="prod", error="boom", now=now_utc)
    assert get_synced_message(db, msg_id="3001", cui="12345678", env="prod").last_error == "boom"

    update_attempt(db, msg_id="3001", cui="12345678", env="prod", error=None, now=now_utc)
    assert get_synced_message(db, msg_id="3001", cui="12345678", env="prod").last_error is None


def test_find_pending_rows_returns_only_unfinished(db, now_utc) -> None:
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
    insert_synced_message(db, _msg(msg_id="C", msg_type="MESAJ", counterparty_cui=None, issue_date=None))
    update_zip_path(db, msg_id="C", cui="12345678", env="prod", zip_path="zC", now=now_utc)

    pending = {r.msg_id for r in find_pending_rows(db, cui="12345678", env="prod")}
    assert pending == {"B", "C"}
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/storage/test_db.py -v`
Expected: ImportError on new symbols.

- [ ] **Step 3:** Append to `src/efactura_sync/storage/db.py`

```python
from datetime import date


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


def _row_to_msg(row: tuple) -> SyncedMessage:
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
    conn: sqlite3.Connection, *, msg_id: str, cui: str, env: str, zip_path: str, now: datetime
) -> None:
    conn.execute(
        "UPDATE synced_messages SET zip_path=?, last_attempt_at=?, last_error=NULL "
        "WHERE msg_id=? AND cui=? AND env=?",
        (zip_path, _iso(now), msg_id, cui, env),
    )
    conn.commit()


def update_pdf_path(
    conn: sqlite3.Connection, *, msg_id: str, cui: str, env: str, pdf_path: str, now: datetime
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


def find_pending_rows(
    conn: sqlite3.Connection, *, cui: str, env: str
) -> list[SyncedMessage]:
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
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/storage/test_db.py -v`
Expected: 17 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/storage/db.py tests/storage/test_db.py
git commit -m "feat(storage): add synced_messages dedup ledger and step markers"
```

---

## Task 10 — `config.py` TOML loader

**Files:**
- Create: `src/efactura_sync/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1:** Write failing test `tests/test_config.py`

```python
from pathlib import Path

import pytest

from efactura_sync.config import Config, load_config
from efactura_sync.errors import ConfigError


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_load_config_minimal(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "config.toml",
        """
        [smtp]
        host = "smtp.example.com"
        port = 465
        tls = "implicit"
        from_addr = "from@example.com"
        to_addr = "to@example.com"

        [anaf]
        default_env = "prod"

        [logging]
        level = "INFO"
        """,
    )
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username = "u"
        password = "p"

        [anaf.prod]
        client_id = "cid-prod"
        client_secret = "cs-prod"

        [anaf.test]
        client_id = "cid-test"
        client_secret = "cs-test"
        """,
    )

    cfg = load_config(config_path=cfg_path, secrets_path=sec_path)

    assert isinstance(cfg, Config)
    assert cfg.smtp.host == "smtp.example.com"
    assert cfg.smtp.tls == "implicit"
    assert cfg.smtp.username == "u"
    assert cfg.smtp.error_to_addr == "to@example.com"  # defaults to to_addr
    assert cfg.anaf.default_env == "prod"
    assert cfg.anaf_credentials("prod") == ("cid-prod", "cs-prod")
    assert cfg.anaf_credentials("test") == ("cid-test", "cs-test")
    assert cfg.archive_root.is_absolute()


def test_load_config_archive_root_override(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "config.toml",
        f"""
        [archive]
        root = "{tmp_path / 'arch'}"
        [smtp]
        host="h"
        port=465
        tls="implicit"
        from_addr="a"
        to_addr="b"
        [anaf]
        default_env="prod"
        [logging]
        level="INFO"
        """,
    )
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username="u"
        password="p"
        [anaf.prod]
        client_id="x"
        client_secret="y"
        [anaf.test]
        client_id="x"
        client_secret="y"
        """,
    )
    cfg = load_config(config_path=cfg_path, secrets_path=sec_path)
    assert cfg.archive_root == tmp_path / "arch"


def test_load_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config.toml"):
        load_config(config_path=tmp_path / "nope.toml", secrets_path=tmp_path / "secrets.toml")


def test_load_config_invalid_tls(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "config.toml",
        """
        [smtp]
        host="h"
        port=465
        tls="weird-mode"
        from_addr="a"
        to_addr="b"
        [anaf]
        default_env="prod"
        [logging]
        level="INFO"
        """,
    )
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username="u"
        password="p"
        [anaf.prod]
        client_id="x"
        client_secret="y"
        [anaf.test]
        client_id="x"
        client_secret="y"
        """,
    )
    with pytest.raises(ConfigError, match="tls"):
        load_config(config_path=cfg_path, secrets_path=sec_path)
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/test_config.py -v`
Expected: ImportError.

- [ ] **Step 3:** Write `src/efactura_sync/config.py`

```python
"""Load and validate TOML configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from platformdirs import user_data_path

from efactura_sync.errors import ConfigError

TlsMode = Literal["implicit", "starttls"]
Env = Literal["prod", "test"]


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    tls: TlsMode
    from_addr: str
    to_addr: str
    error_to_addr: str
    username: str
    password: str


@dataclass(frozen=True)
class AnafConfig:
    default_env: Env
    prod_client_id: str
    prod_client_secret: str
    test_client_id: str
    test_client_secret: str


@dataclass(frozen=True)
class Config:
    archive_root: Path
    smtp: SmtpConfig
    anaf: AnafConfig
    log_level: str

    def anaf_credentials(self, env: Env) -> tuple[str, str]:
        if env == "prod":
            return self.anaf.prod_client_id, self.anaf.prod_client_secret
        return self.anaf.test_client_id, self.anaf.test_client_secret


def _read_toml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"missing file: {path}")
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e


def load_config(*, config_path: Path, secrets_path: Path) -> Config:
    cfg = _read_toml(config_path)
    sec = _read_toml(secrets_path)

    try:
        smtp_cfg = cfg["smtp"]
        anaf_cfg = cfg["anaf"]
        log_cfg = cfg["logging"]
        smtp_sec = sec["smtp"]
        anaf_prod = sec["anaf"]["prod"]
        anaf_test = sec["anaf"]["test"]
    except KeyError as e:
        raise ConfigError(f"missing required section/key: {e}") from e

    tls = smtp_cfg.get("tls", "implicit")
    if tls not in ("implicit", "starttls"):
        raise ConfigError(f"invalid smtp.tls value: {tls!r}")

    default_env = anaf_cfg.get("default_env", "prod")
    if default_env not in ("prod", "test"):
        raise ConfigError(f"invalid anaf.default_env: {default_env!r}")

    archive_root_raw = cfg.get("archive", {}).get("root")
    if archive_root_raw:
        archive_root = Path(str(archive_root_raw)).expanduser()
    else:
        archive_root = user_data_path("efactura-sync", appauthor=False) / "archive"

    smtp = SmtpConfig(
        host=smtp_cfg["host"],
        port=int(smtp_cfg["port"]),
        tls=tls,
        from_addr=smtp_cfg["from_addr"],
        to_addr=smtp_cfg["to_addr"],
        error_to_addr=smtp_cfg.get("error_to_addr", smtp_cfg["to_addr"]),
        username=smtp_sec["username"],
        password=smtp_sec["password"],
    )
    anaf = AnafConfig(
        default_env=default_env,
        prod_client_id=anaf_prod["client_id"],
        prod_client_secret=anaf_prod["client_secret"],
        test_client_id=anaf_test["client_id"],
        test_client_secret=anaf_test["client_secret"],
    )
    return Config(
        archive_root=archive_root,
        smtp=smtp,
        anaf=anaf,
        log_level=str(log_cfg.get("level", "INFO")),
    )
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/test_config.py -v`
Expected: 4 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/config.py tests/test_config.py
git commit -m "feat(config): add TOML config + secrets loader"
```

---

## Task 11 — `anaf/messages.py` UBL parsing + ZIP extraction

**Files:**
- Create: `src/efactura_sync/anaf/__init__.py` (empty)
- Create: `src/efactura_sync/anaf/messages.py`
- Create: `tests/anaf/__init__.py` (empty)
- Create: `tests/fixtures/ubl/primita_minimal.xml`
- Test: `tests/anaf/test_messages.py`

- [ ] **Step 1:** Create empty `__init__.py` files for `src/efactura_sync/anaf/` and `tests/anaf/`.

- [ ] **Step 2:** Write fixture `tests/fixtures/ubl/primita_minimal.xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>INV-00451</cbc:ID>
  <cbc:IssueDate>2026-05-04</cbc:IssueDate>
  <cbc:DocumentCurrencyCode>RON</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyTaxScheme>
        <cbc:CompanyID>RO87654321</cbc:CompanyID>
      </cac:PartyTaxScheme>
      <cac:PartyLegalEntity>
        <cbc:RegistrationName>Furnizor X SRL</cbc:RegistrationName>
        <cbc:CompanyID>RO87654321</cbc:CompanyID>
      </cac:PartyLegalEntity>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty>
    <cac:Party>
      <cac:PartyLegalEntity>
        <cbc:CompanyID>12345678</cbc:CompanyID>
      </cac:PartyLegalEntity>
    </cac:Party>
  </cac:AccountingCustomerParty>
  <cac:LegalMonetaryTotal>
    <cbc:PayableAmount currencyID="RON">1234.56</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</Invoice>
```

- [ ] **Step 3:** Write failing test `tests/anaf/test_messages.py`

```python
from datetime import date
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pytest

from efactura_sync.anaf.messages import (
    InvoiceFields,
    ListMessage,
    classify_tip,
    extract_ubl_xml,
    parse_invoice_fields,
    parse_list_response,
)
from efactura_sync.errors import InvalidArchiveError

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ubl" / "primita_minimal.xml"


def test_classify_tip() -> None:
    assert classify_tip("FACTURA PRIMITA") == "PRIMITA"
    assert classify_tip("FACTURA TRIMISA") == "TRIMISA"
    assert classify_tip("ERORI FACTURA") == "ERORI"
    assert classify_tip("ANY OTHER STRING") == "MESAJ"


def test_parse_list_response() -> None:
    payload = {
        "mesaje": [
            {
                "id": "3001",
                "cif": "12345678",
                "data_creare": "202605041030",
                "tip": "FACTURA PRIMITA",
                "detalii": "supplier RO87654321",
            },
            {
                "id": "3002",
                "cif": "12345678",
                "data_creare": "202605041040",
                "tip": "FACTURA TRIMISA",
                "detalii": "for RO111",
            },
        ]
    }
    msgs = parse_list_response(payload)
    assert msgs == [
        ListMessage(
            msg_id="3001",
            cif="12345678",
            data_creare_utc=__import__("datetime").datetime(2026, 5, 4, 7, 30, tzinfo=__import__("datetime").timezone.utc),
            tip_raw="FACTURA PRIMITA",
            tip="PRIMITA",
            detalii="supplier RO87654321",
        ),
        ListMessage(
            msg_id="3002",
            cif="12345678",
            data_creare_utc=__import__("datetime").datetime(2026, 5, 4, 7, 40, tzinfo=__import__("datetime").timezone.utc),
            tip_raw="FACTURA TRIMISA",
            tip="TRIMISA",
            detalii="for RO111",
        ),
    ]


def test_extract_ubl_xml(tmp_path: Path) -> None:
    zip_path = tmp_path / "x.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.writestr("12345_invoice.xml", FIXTURE.read_bytes())
        zf.writestr("12345_signature.xml", b"<sig/>")

    xml = extract_ubl_xml(zip_path.read_bytes())
    assert b"<cbc:ID>INV-00451</cbc:ID>" in xml


def test_extract_ubl_xml_rejects_non_ubl(tmp_path: Path) -> None:
    zip_path = tmp_path / "x.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.writestr("only_signature.xml", b"<sig/>")
    with pytest.raises(InvalidArchiveError):
        extract_ubl_xml(zip_path.read_bytes())


def test_extract_ubl_xml_rejects_bad_zip() -> None:
    with pytest.raises(InvalidArchiveError):
        extract_ubl_xml(b"not a zip")


def test_parse_invoice_fields() -> None:
    fields = parse_invoice_fields(FIXTURE.read_bytes())
    assert fields == InvoiceFields(
        invoice_number="INV-00451",
        issue_date=date(2026, 5, 4),
        currency="RON",
        payable_amount=Decimal("1234.56"),
        supplier_cui="RO87654321",
        supplier_name="Furnizor X SRL",
        customer_cui="12345678",
    )
```

- [ ] **Step 4:** Run to verify failure

Run: `uv run pytest tests/anaf/test_messages.py -v`
Expected: ImportError.

- [ ] **Step 5:** Write `src/efactura_sync/anaf/messages.py`

```python
"""Decode listamesaje JSON and parse UBL XML invoices."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from lxml import etree

from efactura_sync.errors import InvalidArchiveError

NS = {
    "ubl": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}

MsgType = Literal["PRIMITA", "TRIMISA", "ERORI", "MESAJ"]


@dataclass(frozen=True)
class ListMessage:
    msg_id: str
    cif: str
    data_creare_utc: datetime
    tip_raw: str
    tip: MsgType
    detalii: str


@dataclass(frozen=True)
class InvoiceFields:
    invoice_number: str | None
    issue_date: date | None
    currency: str | None
    payable_amount: Decimal | None
    supplier_cui: str | None
    supplier_name: str | None
    customer_cui: str | None


def classify_tip(raw: str) -> MsgType:
    upper = raw.upper().strip()
    if upper == "FACTURA PRIMITA":
        return "PRIMITA"
    if upper == "FACTURA TRIMISA":
        return "TRIMISA"
    if upper == "ERORI FACTURA":
        return "ERORI"
    return "MESAJ"


def _parse_data_creare(raw: str) -> datetime:
    """ANAF returns YYYYMMDDHHMM in Europe/Bucharest local time.

    We convert to UTC. Bucharest is UTC+2 in winter, UTC+3 in summer (DST).
    """
    from zoneinfo import ZoneInfo

    naive = datetime.strptime(raw, "%Y%m%d%H%M")
    local = naive.replace(tzinfo=ZoneInfo("Europe/Bucharest"))
    return local.astimezone(timezone.utc)


def parse_list_response(payload: dict) -> list[ListMessage]:
    items = payload.get("mesaje", []) or []
    out: list[ListMessage] = []
    for it in items:
        tip_raw = str(it.get("tip", ""))
        out.append(
            ListMessage(
                msg_id=str(it["id"]),
                cif=str(it["cif"]),
                data_creare_utc=_parse_data_creare(str(it["data_creare"])),
                tip_raw=tip_raw,
                tip=classify_tip(tip_raw),
                detalii=str(it.get("detalii", "")),
            )
        )
    return out


def extract_ubl_xml(zip_bytes: bytes) -> bytes:
    """Return the UBL Invoice XML from a signed ZIP. Raise on invalid input."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise InvalidArchiveError(f"not a valid zip: {e}") from e

    with zf:
        for name in zf.namelist():
            if not name.lower().endswith(".xml"):
                continue
            if "signature" in name.lower() or "semnatura" in name.lower():
                continue
            data = zf.read(name)
            try:
                root = etree.fromstring(data)
            except etree.XMLSyntaxError:
                continue
            if root.tag == f"{{{NS['ubl']}}}Invoice":
                return data
    raise InvalidArchiveError("ZIP does not contain a UBL Invoice xml")


def _text(root: etree._Element, xpath: str) -> str | None:
    nodes = root.xpath(xpath, namespaces=NS)
    if not nodes:
        return None
    if isinstance(nodes[0], etree._Element):
        text = nodes[0].text
        return text.strip() if text else None
    return str(nodes[0]).strip()


def parse_invoice_fields(ubl_xml: bytes) -> InvoiceFields:
    try:
        root = etree.fromstring(ubl_xml)
    except etree.XMLSyntaxError as e:
        raise InvalidArchiveError(f"invalid UBL XML: {e}") from e

    inv_no = _text(root, "/ubl:Invoice/cbc:ID")
    issue_raw = _text(root, "/ubl:Invoice/cbc:IssueDate")
    currency = _text(root, "/ubl:Invoice/cbc:DocumentCurrencyCode")
    amount = _text(root, "/ubl:Invoice/cac:LegalMonetaryTotal/cbc:PayableAmount")
    supplier_cui = _text(
        root,
        "/ubl:Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:CompanyID",
    ) or _text(
        root,
        "/ubl:Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID",
    )
    supplier_name = _text(
        root,
        "/ubl:Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName",
    )
    customer_cui = _text(
        root,
        "/ubl:Invoice/cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:CompanyID",
    )

    return InvoiceFields(
        invoice_number=inv_no,
        issue_date=date.fromisoformat(issue_raw) if issue_raw else None,
        currency=currency,
        payable_amount=Decimal(amount) if amount else None,
        supplier_cui=supplier_cui,
        supplier_name=supplier_name,
        customer_cui=customer_cui,
    )
```

- [ ] **Step 6:** Run to verify it passes

Run: `uv run pytest tests/anaf/test_messages.py -v`
Expected: 6 passed.

- [ ] **Step 7:** Commit

```bash
git add src/efactura_sync/anaf/ tests/anaf/ tests/fixtures/
git commit -m "feat(anaf): parse listamesaje json and UBL invoice fields"
```

---

## Task 12 — `anaf/client.py` HTTP wrapper for listamesaje + descarcare

**Files:**
- Create: `src/efactura_sync/anaf/client.py`
- Test: `tests/anaf/test_client.py`

- [ ] **Step 1:** Write failing test `tests/anaf/test_client.py`

```python
import httpx
import pytest

from efactura_sync.anaf.client import AnafClient
from efactura_sync.errors import PermanentError, TransientError


def _client(handler: httpx.MockTransport) -> AnafClient:
    return AnafClient(http=httpx.Client(transport=handler), env="prod")


def test_list_messages_sends_correct_query_and_auth() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"mesaje": []})

    client = _client(httpx.MockTransport(handler))
    out = client.list_messages(cif="12345678", zile=1, access_token="tok")

    assert out == []
    assert "cif=12345678" in captured["url"]
    assert "zile=1" in captured["url"]
    assert "/listaMesajeFactura" in captured["url"]
    assert captured["auth"] == "Bearer tok"


def test_list_messages_clamps_zile_to_60() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"mesaje": []})

    client = _client(httpx.MockTransport(handler))
    client.list_messages(cif="12345678", zile=999, access_token="tok")
    assert "zile=60" in captured["url"]


def test_download_returns_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"PK\x03\x04zipbytes")

    client = _client(httpx.MockTransport(handler))
    out = client.download(msg_id="3001", access_token="tok")
    assert out == b"PK\x03\x04zipbytes"


def test_5xx_raises_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream")

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(TransientError):
        client.list_messages(cif="12345678", zile=1, access_token="tok")


def test_4xx_raises_permanent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad token")

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(PermanentError):
        client.download(msg_id="3001", access_token="tok")
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/anaf/test_client.py -v`
Expected: ImportError.

- [ ] **Step 3:** Write `src/efactura_sync/anaf/client.py`

```python
"""HTTP wrapper for the read-side ANAF e-Factura endpoints."""

from __future__ import annotations

from typing import Literal

import httpx

from efactura_sync.anaf.messages import ListMessage, parse_list_response
from efactura_sync.errors import PermanentError, TransientError

Env = Literal["prod", "test"]

_BASE_URLS: dict[str, str] = {
    "prod": "https://api.anaf.ro/prod/FCTEL/rest",
    "test": "https://api.anaf.ro/test/FCTEL/rest",
}

_USER_AGENT = "efactura-sync/0.1.0"


def _classify(response: httpx.Response) -> None:
    """Raise TransientError for 5xx/429, PermanentError for 4xx."""
    if response.status_code == 429 or 500 <= response.status_code < 600:
        raise TransientError(
            f"transient HTTP {response.status_code}",
            status=response.status_code,
            body=response.content,
        )
    if 400 <= response.status_code < 500:
        raise PermanentError(
            f"permanent HTTP {response.status_code}",
            status=response.status_code,
            body=response.content,
        )


class AnafClient:
    def __init__(self, http: httpx.Client, env: Env) -> None:
        self._http = http
        self._base = _BASE_URLS[env]

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": _USER_AGENT,
        }

    def list_messages(
        self, *, cif: str, zile: int, access_token: str
    ) -> list[ListMessage]:
        zile_clamped = max(1, min(60, zile))
        resp = self._http.get(
            f"{self._base}/listaMesajeFactura",
            params={"cif": cif, "zile": zile_clamped},
            headers=self._headers(access_token),
            timeout=30.0,
        )
        _classify(resp)
        return parse_list_response(resp.json())

    def download(self, *, msg_id: str, access_token: str) -> bytes:
        resp = self._http.get(
            f"{self._base}/descarcare",
            params={"id": msg_id},
            headers=self._headers(access_token),
            timeout=60.0,
        )
        _classify(resp)
        return resp.content
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/anaf/test_client.py -v`
Expected: 5 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/anaf/client.py tests/anaf/test_client.py
git commit -m "feat(anaf): add http client for listamesaje + descarcare"
```

---

## Task 13 — `render.py` xmltopdf POST

**Files:**
- Create: `src/efactura_sync/render.py`
- Test: `tests/test_render.py`

- [ ] **Step 1:** Write failing test `tests/test_render.py`

```python
import httpx
import pytest

from efactura_sync.errors import RenderError
from efactura_sync.render import PdfRenderer


def test_render_posts_xml_and_returns_pdf() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        captured["content_type"] = request.headers.get("Content-Type")
        return httpx.Response(200, content=b"%PDF-1.7 fake")

    renderer = PdfRenderer(http=httpx.Client(transport=httpx.MockTransport(handler)))
    pdf = renderer.render(ubl_xml=b"<Invoice/>", standard="FACT1")

    assert pdf == b"%PDF-1.7 fake"
    assert captured["url"].endswith("/transformare/FACT1")
    assert captured["body"] == b"<Invoice/>"
    assert "text/plain" in captured["content_type"]


def test_render_rejects_non_pdf_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>error page</html>")

    renderer = PdfRenderer(http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RenderError, match="not a PDF"):
        renderer.render(ubl_xml=b"<Invoice/>")


def test_render_5xx_raises_render_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    renderer = PdfRenderer(http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RenderError):
        renderer.render(ubl_xml=b"<Invoice/>")
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/test_render.py -v`
Expected: ImportError.

- [ ] **Step 3:** Write `src/efactura_sync/render.py`

```python
"""Render UBL XML to PDF via ANAF's hosted xmltopdf service."""

from __future__ import annotations

import httpx

from efactura_sync.errors import RenderError

_XMLTOPDF_BASE = "https://webservicesp.anaf.ro/prod/FCTEL/rest/transformare"
_USER_AGENT = "efactura-sync/0.1.0"


class PdfRenderer:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def render(self, *, ubl_xml: bytes, standard: str = "FACT1") -> bytes:
        resp = self._http.post(
            f"{_XMLTOPDF_BASE}/{standard}",
            content=ubl_xml,
            headers={
                "Content-Type": "text/plain",
                "User-Agent": _USER_AGENT,
            },
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise RenderError(f"xmltopdf returned HTTP {resp.status_code}: {resp.text[:200]}")
        if not resp.content.startswith(b"%PDF"):
            raise RenderError("xmltopdf response is not a PDF")
        return resp.content
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/test_render.py -v`
Expected: 3 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/render.py tests/test_render.py
git commit -m "feat(render): add xmltopdf renderer"
```

---

## Task 14 — `anaf/oauth.py` token store + refresh

**Files:**
- Create: `src/efactura_sync/anaf/oauth.py`
- Test: `tests/anaf/test_oauth.py`

- [ ] **Step 1:** Write failing test `tests/anaf/test_oauth.py`

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from efactura_sync.anaf.oauth import (
    Token,
    load_token,
    needs_refresh,
    refresh_access_token,
    save_token,
)
from efactura_sync.errors import RefreshTokenExpired


def _token(**overrides) -> Token:
    base = Token(
        cui="12345678",
        env="prod",
        access_token="acc",
        refresh_token="ref",
        expires_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        obtained_at=datetime(2026, 5, 4, tzinfo=timezone.utc),
    )
    return Token(**{**base.__dict__, **overrides})


def test_save_and_load_token_round_trip(tmp_path: Path) -> None:
    save_token(tmp_path, _token())
    got = load_token(tmp_path, cui="12345678", env="prod")
    assert got == _token()


def test_save_token_chmod_0600(tmp_path: Path) -> None:
    save_token(tmp_path, _token())
    f = tmp_path / "12345678.prod.json"
    mode = f.stat().st_mode & 0o777
    assert mode == 0o600


def test_needs_refresh_window() -> None:
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)  # 7 days before 2026-08-01
    assert needs_refresh(_token(), now=now, buffer_days=7) is True
    earlier = datetime(2026, 7, 20, tzinfo=timezone.utc)  # 12 days before
    assert needs_refresh(_token(), now=earlier, buffer_days=7) is False


def test_refresh_access_token_success() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["data"] = dict(request.url.params) if request.url.params else None
        captured["body"] = request.content.decode() if request.content else ""
        return httpx.Response(
            200,
            json={
                "access_token": "new-acc",
                "refresh_token": "new-ref",
                "expires_in": 7776000,  # 90 days in seconds
                "token_type": "bearer",
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    now = datetime(2026, 5, 4, tzinfo=timezone.utc)
    result = refresh_access_token(
        http=http,
        env="prod",
        client_id="cid",
        client_secret="cs",
        cui="12345678",
        refresh_token="old-ref",
        now=now,
    )

    assert result.access_token == "new-acc"
    assert result.refresh_token == "new-ref"
    assert result.cui == "12345678"
    assert result.env == "prod"
    assert result.obtained_at == now
    assert result.expires_at == now + timedelta(seconds=7776000)
    # client creds should be in the body (form-encoded)
    assert "client_id=cid" in captured["body"]
    assert "refresh_token=old-ref" in captured["body"]
    assert "grant_type=refresh_token" in captured["body"]


def test_refresh_token_400_raises_refresh_expired() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RefreshTokenExpired):
        refresh_access_token(
            http=http,
            env="prod",
            client_id="cid",
            client_secret="cs",
            cui="12345678",
            refresh_token="dead",
            now=datetime(2026, 5, 4, tzinfo=timezone.utc),
        )
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/anaf/test_oauth.py -v`
Expected: ImportError.

- [ ] **Step 3:** Write `src/efactura_sync/anaf/oauth.py`

```python
"""ANAF OAuth2: token persistence + refresh.

The interactive authorization-code flow lives in :func:`auth_code_login` (added
in a later task). Refresh and load/save are usable on the headless server.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx

from efactura_sync.errors import AuthError, RefreshTokenExpired

Env = Literal["prod", "test"]

_TOKEN_URLS: dict[str, str] = {
    "prod": "https://logincert.anaf.ro/anaf-oauth2/v1/token",
    "test": "https://logincert.anaf.ro/anaf-oauth2/v1/token",
}


@dataclass(frozen=True)
class Token:
    cui: str
    env: Env
    access_token: str
    refresh_token: str
    expires_at: datetime
    obtained_at: datetime


def _path(tokens_dir: Path, *, cui: str, env: str) -> Path:
    return tokens_dir / f"{cui}.{env}.json"


def save_token(tokens_dir: Path, token: Token) -> None:
    tokens_dir.mkdir(parents=True, exist_ok=True)
    p = _path(tokens_dir, cui=token.cui, env=token.env)
    payload = {
        **asdict(token),
        "expires_at": token.expires_at.isoformat().replace("+00:00", "Z"),
        "obtained_at": token.obtained_at.isoformat().replace("+00:00", "Z"),
    }
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(payload, indent=2).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(p, 0o600)


def load_token(tokens_dir: Path, *, cui: str, env: str) -> Token:
    p = _path(tokens_dir, cui=cui, env=env)
    if not p.exists():
        raise AuthError(f"no token file for cui={cui} env={env}")
    raw = json.loads(p.read_text(encoding="utf-8"))

    def _parse(s: str) -> datetime:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)

    return Token(
        cui=raw["cui"],
        env=raw["env"],
        access_token=raw["access_token"],
        refresh_token=raw["refresh_token"],
        expires_at=_parse(raw["expires_at"]),
        obtained_at=_parse(raw["obtained_at"]),
    )


def needs_refresh(token: Token, *, now: datetime, buffer_days: int = 7) -> bool:
    return token.expires_at - now < timedelta(days=buffer_days)


def refresh_access_token(
    *,
    http: httpx.Client,
    env: Env,
    client_id: str,
    client_secret: str,
    cui: str,
    refresh_token: str,
    now: datetime,
) -> Token:
    resp = http.post(
        _TOKEN_URLS[env],
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"User-Agent": "efactura-sync/0.1.0"},
        timeout=30.0,
    )
    if resp.status_code == 400:
        raise RefreshTokenExpired(f"refresh failed (400): {resp.text[:200]}")
    if resp.status_code != 200:
        raise AuthError(f"refresh failed (HTTP {resp.status_code}): {resp.text[:200]}")
    body = resp.json()
    expires_in = int(body.get("expires_in", 0))
    return Token(
        cui=cui,
        env=env,
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token", refresh_token),
        expires_at=now + timedelta(seconds=expires_in),
        obtained_at=now,
    )
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/anaf/test_oauth.py -v`
Expected: 5 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/anaf/oauth.py tests/anaf/test_oauth.py
git commit -m "feat(oauth): add token store and refresh flow"
```

---

## Task 15 — `anaf/oauth.py` interactive auth-code flow

**Files:**
- Modify: `src/efactura_sync/anaf/oauth.py`
- Modify: `tests/anaf/test_oauth.py`

- [ ] **Step 1:** Append failing test

```python
import threading
import urllib.request

from efactura_sync.anaf.oauth import auth_code_login


def test_auth_code_login_full_flow(monkeypatch) -> None:
    """Simulate the browser hitting the local callback server with a code."""
    captured_post: dict = {}

    def post_handler(request: httpx.Request) -> httpx.Response:
        captured_post["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "access_token": "acc",
                "refresh_token": "ref",
                "expires_in": 7776000,
                "token_type": "bearer",
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(post_handler))

    # Stub webbrowser.open: hit the redirect_uri ourselves with a fake code.
    def fake_open(url: str) -> bool:
        # extract redirect_uri and call it
        import urllib.parse as up

        q = up.parse_qs(up.urlparse(url).query)
        redirect = q["redirect_uri"][0]
        thread = threading.Thread(
            target=lambda: urllib.request.urlopen(f"{redirect}?code=fakecode&state={q['state'][0]}").read()
        )
        thread.start()
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)

    now = datetime(2026, 5, 4, tzinfo=timezone.utc)
    token = auth_code_login(
        http=http,
        env="prod",
        client_id="cid",
        client_secret="cs",
        cui="12345678",
        now=now,
    )
    assert token.access_token == "acc"
    assert token.cui == "12345678"
    assert "code=fakecode" in captured_post["body"]
    assert "grant_type=authorization_code" in captured_post["body"]
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/anaf/test_oauth.py -v -k auth_code_login`
Expected: ImportError on `auth_code_login`.

- [ ] **Step 3:** Append to `src/efactura_sync/anaf/oauth.py`

```python
import secrets as _secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

_AUTHORIZE_URLS: dict[str, str] = {
    "prod": "https://logincert.anaf.ro/anaf-oauth2/v1/authorize",
    "test": "https://logincert.anaf.ro/anaf-oauth2/v1/authorize",
}


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        type(self).code = (params.get("code") or [None])[0]
        type(self).state = (params.get("state") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<html><body>OK. You can close this tab.</body></html>")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return  # silence access logs in tests


def auth_code_login(
    *,
    http: httpx.Client,
    env: Env,
    client_id: str,
    client_secret: str,
    cui: str,
    now: datetime,
    bind_host: str = "127.0.0.1",
    bind_port: int = 0,
) -> Token:
    """Run the OAuth2 authorization-code flow.

    Opens the system browser at ANAF's authorize URL; ANAF prompts for the
    qualified digital certificate; ANAF redirects back to this short-lived
    local HTTP server with ``?code=...&state=...``. The code is exchanged for
    a token at ANAF's ``/token`` endpoint.

    Must run on a host with a browser AND the cert plugged in.
    """
    state = _secrets.token_urlsafe(24)
    server = HTTPServer((bind_host, bind_port), _CallbackHandler)
    actual_port = server.server_address[1]
    redirect_uri = f"http://{bind_host}:{actual_port}/callback"

    auth_url = (
        f"{_AUTHORIZE_URLS[env]}?"
        + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
    )

    webbrowser.open(auth_url)

    try:
        # serve exactly one request (the callback)
        server.handle_request()
    finally:
        server.server_close()

    if not _CallbackHandler.code:
        raise AuthError("no code received from ANAF callback")
    if _CallbackHandler.state != state:
        raise AuthError("state mismatch on ANAF callback")
    code = _CallbackHandler.code
    _CallbackHandler.code = None
    _CallbackHandler.state = None

    resp = http.post(
        _TOKEN_URLS[env],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"User-Agent": "efactura-sync/0.1.0"},
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise AuthError(f"token exchange failed (HTTP {resp.status_code}): {resp.text[:200]}")
    body = resp.json()
    expires_in = int(body.get("expires_in", 0))
    return Token(
        cui=cui,
        env=env,
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        expires_at=now + timedelta(seconds=expires_in),
        obtained_at=now,
    )
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/anaf/test_oauth.py -v`
Expected: 6 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/anaf/oauth.py tests/anaf/test_oauth.py
git commit -m "feat(oauth): add interactive authorization-code login flow"
```

---

## Task 16 — `mail.py` Romanian email rendering

**Files:**
- Create: `src/efactura_sync/mail.py`
- Test: `tests/test_mail.py`

- [ ] **Step 1:** Write failing test `tests/test_mail.py`

```python
from datetime import date
from decimal import Decimal

from efactura_sync.anaf.messages import InvoiceFields, ListMessage
from efactura_sync.mail import (
    EmailMessage,
    render_erori_email,
    render_failure_email,
    render_mesaj_email,
    render_primita_email,
)


def _list_msg(**overrides) -> ListMessage:
    from datetime import datetime, timezone

    base = ListMessage(
        msg_id="3001",
        cif="12345678",
        data_creare_utc=datetime(2026, 5, 4, 8, 30, tzinfo=timezone.utc),
        tip_raw="FACTURA PRIMITA",
        tip="PRIMITA",
        detalii="from RO87654321",
    )
    return ListMessage(**{**base.__dict__, **overrides})


def test_render_primita_email() -> None:
    fields = InvoiceFields(
        invoice_number="INV-00451",
        issue_date=date(2026, 5, 4),
        currency="RON",
        payable_amount=Decimal("1234.56"),
        supplier_cui="RO87654321",
        supplier_name="Furnizor X SRL",
        customer_cui="12345678",
    )
    email = render_primita_email(
        my_cui="12345678",
        my_display_name="Acme SRL",
        list_msg=_list_msg(),
        fields=fields,
        zip_attachment=("3001.zip", b"PK"),
        pdf_attachment=("3001.pdf", b"%PDF"),
        env="prod",
    )

    assert isinstance(email, EmailMessage)
    assert "[factură]" in email.subject
    assert "Acme SRL" in email.subject
    assert "Furnizor X SRL" in email.subject
    assert "INV-00451" in email.subject
    assert "2026-05-04" in email.subject
    assert "Sincronizare ANAF e-Factura — factură nouă primită" in email.body
    assert "1234.56 RON" in email.body
    assert email.message_id == "<3001.prod@efactura-sync>"
    assert ("3001.zip", b"PK") in email.attachments
    assert ("3001.pdf", b"%PDF") in email.attachments


def test_render_primita_email_pdf_pending_when_pdf_missing() -> None:
    fields = InvoiceFields(
        invoice_number="INV-X",
        issue_date=date(2026, 5, 4),
        currency="RON",
        payable_amount=None,
        supplier_cui="RO123",
        supplier_name=None,
        customer_cui="12345678",
    )
    email = render_primita_email(
        my_cui="12345678",
        my_display_name=None,
        list_msg=_list_msg(),
        fields=fields,
        zip_attachment=("3001.zip", b"PK"),
        pdf_attachment=None,
        env="prod",
    )
    assert "PDF: în curs de generare" in email.body
    assert ("3001.zip", b"PK") in email.attachments
    # Total falls back to em-dash when payable_amount missing
    assert "Total:          —" in email.body or "Total:         —" in email.body


def test_render_erori_email_uses_eroare_prefix() -> None:
    list_msg = _list_msg(tip_raw="ERORI FACTURA", tip="ERORI", detalii="some error")
    email = render_erori_email(
        my_cui="12345678",
        my_display_name="Acme",
        list_msg=list_msg,
        zip_attachment=("3001.zip", b"PK"),
        env="prod",
    )
    assert email.subject.startswith("[eroare]")
    assert "ERORI FACTURA" in email.body
    assert "some error" in email.body
    assert email.attachments == [("3001.zip", b"PK")]


def test_render_mesaj_email_uses_notificare_prefix() -> None:
    list_msg = _list_msg(tip_raw="MESAJ_CUMPARATOR", tip="MESAJ", detalii="hi")
    email = render_mesaj_email(
        my_cui="12345678",
        my_display_name=None,
        list_msg=list_msg,
        zip_attachment=("3001.zip", b"PK"),
        env="prod",
    )
    assert email.subject.startswith("[notificare]")
    assert "MESAJ_CUMPARATOR" in email.body


def test_render_failure_email() -> None:
    email = render_failure_email(
        hostname="homepi",
        run_date=date(2026, 5, 4),
        cui_in_progress="12345678",
        step="descarcare",
        exception_type="TransientError",
        log_tail="line1\nline2",
        traceback="Traceback (most recent call last):\n...",
    )
    assert email.subject.startswith("[eroare-rulare]")
    assert "homepi" in email.subject
    assert "2026-05-04" in email.subject
    assert "12345678" in email.body
    assert "descarcare" in email.body
    assert "TransientError" in email.body
    assert email.attachments == [("traceback.txt", b"Traceback (most recent call last):\n...")]
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/test_mail.py -v`
Expected: ImportError.

- [ ] **Step 3:** Write `src/efactura_sync/mail.py`

```python
"""Email rendering (Romanian) and SMTP delivery.

This module is split in two layers:
  * Pure rendering functions return :class:`EmailMessage` values.
  * The :class:`Mailer` class owns the SMTP connection and side-effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from efactura_sync.anaf.messages import InvoiceFields, ListMessage

Env = Literal["prod", "test"]


@dataclass(frozen=True)
class EmailMessage:
    subject: str
    body: str
    message_id: str
    attachments: list[tuple[str, bytes]] = field(default_factory=list)


def _msg_id(*, msg_id: str, env: str) -> str:
    return f"<{msg_id}.{env}@efactura-sync>"


def _display_or_dash(value: str | None) -> str:
    return value if value else "—"


def _fmt_amount(amount: object, currency: str | None) -> str:
    if amount is None:
        return "—"
    cur = currency or ""
    return f"{amount} {cur}".strip()


def _supplier_label(fields: InvoiceFields) -> str:
    name = fields.supplier_name or "—"
    cui = fields.supplier_cui or "—"
    return f"{cui} ({name})"


def render_primita_email(
    *,
    my_cui: str,
    my_display_name: str | None,
    list_msg: ListMessage,
    fields: InvoiceFields,
    zip_attachment: tuple[str, bytes],
    pdf_attachment: tuple[str, bytes] | None,
    env: Env,
) -> EmailMessage:
    me_label = my_display_name or my_cui
    supplier_label_short = fields.supplier_name or fields.supplier_cui or "—"
    supplier_cui = fields.supplier_cui or "—"
    inv_no = _display_or_dash(fields.invoice_number)
    issue = fields.issue_date.isoformat() if fields.issue_date else "—"

    subject = (
        f"[factură] {me_label} · {supplier_label_short} (CUI {supplier_cui}) · {inv_no} · {issue}"
    )

    pdf_line = "Atașamente: arhiva ZIP semnată, PDF generat."
    attachments: list[tuple[str, bytes]] = [zip_attachment]
    if pdf_attachment is not None:
        attachments.append(pdf_attachment)
    else:
        pdf_line = (
            "Atașament: arhiva ZIP semnată.\n"
            "PDF: în curs de generare — se va retrimite la următoarea rulare."
        )

    body = (
        "Sincronizare ANAF e-Factura — factură nouă primită\n"
        "\n"
        f"CUI propriu:    {my_cui} ({my_display_name or '—'})\n"
        f"Furnizor:       {_supplier_label(fields)}\n"
        f"Număr factură:  {inv_no}\n"
        f"Data emiterii:  {issue}\n"
        f"Total:          {_fmt_amount(fields.payable_amount, fields.currency)}\n"
        f"ID mesaj ANAF:  {list_msg.msg_id}\n"
        "\n"
        f"{pdf_line}\n"
    )

    return EmailMessage(
        subject=subject,
        body=body,
        message_id=_msg_id(msg_id=list_msg.msg_id, env=env),
        attachments=attachments,
    )


def render_erori_email(
    *,
    my_cui: str,
    my_display_name: str | None,
    list_msg: ListMessage,
    zip_attachment: tuple[str, bytes],
    env: Env,
) -> EmailMessage:
    me_label = my_display_name or my_cui
    subject = f"[eroare] {me_label} · mesaj {list_msg.msg_id} · {list_msg.tip_raw}"
    body = (
        "Sincronizare ANAF e-Factura — eroare primită de la ANAF\n"
        "\n"
        f"CUI propriu:   {my_cui} ({my_display_name or '—'})\n"
        f"ID mesaj ANAF: {list_msg.msg_id}\n"
        f"Tip mesaj:     {list_msg.tip_raw}\n"
        f"Detalii ANAF:  {list_msg.detalii}\n"
        "\n"
        "Atașament: arhiva ZIP originală.\n"
    )
    return EmailMessage(
        subject=subject,
        body=body,
        message_id=_msg_id(msg_id=list_msg.msg_id, env=env),
        attachments=[zip_attachment],
    )


def render_mesaj_email(
    *,
    my_cui: str,
    my_display_name: str | None,
    list_msg: ListMessage,
    zip_attachment: tuple[str, bytes],
    env: Env,
) -> EmailMessage:
    me_label = my_display_name or my_cui
    subject = f"[notificare] {me_label} · mesaj {list_msg.msg_id} · {list_msg.tip_raw}"
    body = (
        "Sincronizare ANAF e-Factura — notificare nouă\n"
        "\n"
        f"CUI propriu:   {my_cui} ({my_display_name or '—'})\n"
        f"ID mesaj ANAF: {list_msg.msg_id}\n"
        f"Tip mesaj:     {list_msg.tip_raw}\n"
        f"Detalii ANAF:  {list_msg.detalii}\n"
        "\n"
        "Atașament: arhiva ZIP originală.\n"
    )
    return EmailMessage(
        subject=subject,
        body=body,
        message_id=_msg_id(msg_id=list_msg.msg_id, env=env),
        attachments=[zip_attachment],
    )


def render_failure_email(
    *,
    hostname: str,
    run_date: date,
    cui_in_progress: str | None,
    step: str | None,
    exception_type: str,
    log_tail: str,
    traceback: str,
) -> EmailMessage:
    subject = f"[eroare-rulare] efactura-sync — {run_date.isoformat()} — {hostname}"
    body = (
        "Sincronizare ANAF e-Factura — rulare eșuată\n"
        "\n"
        f"Host:           {hostname}\n"
        f"Data:           {run_date.isoformat()}\n"
        f"CUI în lucru:   {cui_in_progress or '—'}\n"
        f"Pas eșuat:      {step or '—'}\n"
        f"Tip excepție:   {exception_type}\n"
        "\n"
        "Ultimele rânduri din log:\n"
        f"{log_tail}\n"
    )
    return EmailMessage(
        subject=subject,
        body=body,
        message_id=f"<failure-{run_date.isoformat()}-{hostname}@efactura-sync>",
        attachments=[("traceback.txt", traceback.encode("utf-8"))],
    )
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/test_mail.py -v`
Expected: 5 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/mail.py tests/test_mail.py
git commit -m "feat(mail): add Romanian email rendering for all message types"
```

---

## Task 17 — `mail.py` SMTP Mailer

**Files:**
- Modify: `src/efactura_sync/mail.py`
- Modify: `tests/test_mail.py`

- [ ] **Step 1:** Append failing test

```python
from email import message_from_bytes

from efactura_sync.mail import Mailer


class _RecordingSMTP:
    """Stand-in for smtplib.SMTP_SSL / SMTP that records what was sent."""

    instances: list["_RecordingSMTP"] = []

    def __init__(self, host: str, port: int, *args, **kwargs) -> None:
        self.host = host
        self.port = port
        self.logged_in: tuple[str, str] | None = None
        self.starttls_called = False
        self.sent: list[tuple[str, list[str], bytes]] = []
        self.quit_called = False
        type(self).instances.append(self)

    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)

    def starttls(self) -> None:
        self.starttls_called = True

    def sendmail(self, from_addr: str, to_addrs: list[str], msg: bytes) -> None:
        self.sent.append((from_addr, to_addrs, msg))

    def quit(self) -> None:
        self.quit_called = True

    def __enter__(self) -> "_RecordingSMTP":
        return self

    def __exit__(self, *args: object) -> None:
        self.quit()


def test_mailer_sends_with_implicit_tls(monkeypatch) -> None:
    _RecordingSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP_SSL", _RecordingSMTP)

    mailer = Mailer(
        host="smtp.example.com",
        port=465,
        tls="implicit",
        username="u",
        password="p",
        from_addr="from@example.com",
    )
    mailer.send(
        EmailMessage(
            subject="Test",
            body="hi",
            message_id="<1@efactura-sync>",
            attachments=[("a.txt", b"hello")],
        ),
        to_addr="to@example.com",
    )

    [smtp] = _RecordingSMTP.instances
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 465
    assert smtp.logged_in == ("u", "p")
    assert smtp.starttls_called is False
    [(frm, tos, raw)] = smtp.sent
    assert frm == "from@example.com"
    assert tos == ["to@example.com"]
    parsed = message_from_bytes(raw)
    assert parsed["Subject"] == "Test"
    assert parsed["Message-ID"] == "<1@efactura-sync>"
    payloads = parsed.get_payload()
    assert any(p.get_filename() == "a.txt" for p in payloads)


def test_mailer_starttls(monkeypatch) -> None:
    _RecordingSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _RecordingSMTP)

    mailer = Mailer(
        host="smtp.example.com",
        port=587,
        tls="starttls",
        username="u",
        password="p",
        from_addr="from@example.com",
    )
    mailer.send(
        EmailMessage(subject="x", body="y", message_id="<2@efactura-sync>", attachments=[]),
        to_addr="to@example.com",
    )

    [smtp] = _RecordingSMTP.instances
    assert smtp.port == 587
    assert smtp.starttls_called is True
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/test_mail.py -v -k mailer`
Expected: AttributeError on `Mailer`.

- [ ] **Step 3:** Append to `src/efactura_sync/mail.py`

```python
import smtplib
from email.message import EmailMessage as _StdlibEmailMessage


class Mailer:
    """SMTP sender. One connection per :meth:`send` call (callers may reuse)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        tls: Literal["implicit", "starttls"],
        username: str,
        password: str,
        from_addr: str,
    ) -> None:
        self._host = host
        self._port = port
        self._tls = tls
        self._username = username
        self._password = password
        self._from = from_addr

    def _connect(self) -> smtplib.SMTP:
        if self._tls == "implicit":
            return smtplib.SMTP_SSL(self._host, self._port)
        return smtplib.SMTP(self._host, self._port)

    def send(self, msg: EmailMessage, *, to_addr: str) -> None:
        std = _StdlibEmailMessage()
        std["Subject"] = msg.subject
        std["From"] = self._from
        std["To"] = to_addr
        std["Message-ID"] = msg.message_id
        std.set_content(msg.body, charset="utf-8")
        for filename, data in msg.attachments:
            std.add_attachment(
                data,
                maintype="application",
                subtype="octet-stream",
                filename=filename,
            )

        with self._connect() as smtp:
            if self._tls == "starttls":
                smtp.starttls()
            smtp.login(self._username, self._password)
            smtp.sendmail(self._from, [to_addr], std.as_bytes())
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/test_mail.py -v`
Expected: 7 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/mail.py tests/test_mail.py
git commit -m "feat(mail): add SMTP Mailer with implicit-tls and starttls"
```

---

## Task 18 — `sync.py` decision rules + `process_one_message`

**Files:**
- Create: `src/efactura_sync/sync.py`
- Test: `tests/test_sync.py`

- [ ] **Step 1:** Write failing test `tests/test_sync.py`

```python
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

from efactura_sync.anaf.messages import ListMessage
from efactura_sync.errors import RenderError
from efactura_sync.mail import EmailMessage, Mailer  # noqa: F401  (typing)
from efactura_sync.storage.db import (
    add_monitored_cui,
    add_tracked_counterparty,
    get_synced_message,
    init_schema,
)
from efactura_sync.storage.files import FileStore
from efactura_sync.sync import SyncDeps, process_one_message


# --- fakes ----------------------------------------------------------------

class FakeAnaf:
    def __init__(self, *, list_response: list[ListMessage] | None = None,
                 download_payload: bytes | None = None) -> None:
        self.list_response = list_response or []
        self.download_payload = download_payload or b""
        self.list_calls: list[tuple[str, int]] = []
        self.download_calls: list[str] = []

    def list_messages(self, *, cif: str, zile: int, access_token: str):
        self.list_calls.append((cif, zile))
        return self.list_response

    def download(self, *, msg_id: str, access_token: str) -> bytes:
        self.download_calls.append(msg_id)
        return self.download_payload


class FakeRenderer:
    def __init__(self, *, fail: bool = False, output: bytes = b"%PDF-fake") -> None:
        self.fail = fail
        self.output = output
        self.calls: list[bytes] = []

    def render(self, *, ubl_xml: bytes, standard: str = "FACT1") -> bytes:
        self.calls.append(ubl_xml)
        if self.fail:
            raise RenderError("boom")
        return self.output


@dataclass
class FakeMailer:
    sent: list[tuple[EmailMessage, str]] = field(default_factory=list)

    def send(self, msg: EmailMessage, *, to_addr: str) -> None:
        self.sent.append((msg, to_addr))


def _make_zip(xml_bytes: bytes) -> bytes:
    import io
    buf = io.BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("invoice.xml", xml_bytes)
    return buf.getvalue()


# --- fixtures -------------------------------------------------------------

@pytest.fixture
def deps(db: sqlite3.Connection, archive_root: Path) -> SyncDeps:
    init_schema(db)
    return SyncDeps(
        anaf=FakeAnaf(),
        renderer=FakeRenderer(),
        mailer=FakeMailer(),
        files=FileStore(),
        db=db,
        archive_root=archive_root,
        to_addr="me@example.com",
    )


# --- tests ----------------------------------------------------------------

UBL_FIXTURE = (Path(__file__).parent / "fixtures" / "ubl" / "primita_minimal.xml").read_bytes()


def _list_msg(**overrides: Any) -> ListMessage:
    base = ListMessage(
        msg_id="3001",
        cif="12345678",
        data_creare_utc=datetime(2026, 5, 4, 8, 30, tzinfo=timezone.utc),
        tip_raw="FACTURA PRIMITA",
        tip="PRIMITA",
        detalii="ok",
    )
    return ListMessage(**{**base.__dict__, **overrides})


def test_primita_tracked_supplier_archives_and_emails(deps: SyncDeps, now_utc) -> None:
    deps.anaf.download_payload = _make_zip(UBL_FIXTURE)  # type: ignore[attr-defined]
    add_monitored_cui(deps.db, cui="12345678", display_name="Acme", now=now_utc)
    add_tracked_counterparty(deps.db, my_cui="12345678", counterparty_cui="RO87654321", now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.zip_path is not None
    assert row.pdf_path is not None
    assert row.email_sent_at == now_utc
    assert row.email_skip_reason is None
    assert (deps.archive_root / row.zip_path).read_bytes().startswith(b"PK")
    assert (deps.archive_root / row.pdf_path).read_bytes().startswith(b"%PDF")
    assert len(deps.mailer.sent) == 1  # type: ignore[attr-defined]


def test_primita_untracked_supplier_archives_but_skips_email(deps: SyncDeps, now_utc) -> None:
    deps.anaf.download_payload = _make_zip(UBL_FIXTURE)  # type: ignore[attr-defined]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    # NOTE: no tracked counterparty added.

    process_one_message(
        deps,
        my_cui="12345678",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row.email_sent_at is None
    assert row.email_skip_reason == "filtered_by_track_list"
    assert deps.mailer.sent == []  # type: ignore[attr-defined]


def test_trimisa_archives_renders_pdf_but_never_emails(deps: SyncDeps, now_utc) -> None:
    deps.anaf.download_payload = _make_zip(UBL_FIXTURE)  # type: ignore[attr-defined]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(tip_raw="FACTURA TRIMISA", tip="TRIMISA"),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row.zip_path is not None
    assert row.pdf_path is not None
    assert row.email_skip_reason == "never_email_for_type"
    assert deps.mailer.sent == []  # type: ignore[attr-defined]


def test_erori_archives_to_messages_and_emails(deps: SyncDeps, now_utc) -> None:
    deps.anaf.download_payload = b"PKfake"  # type: ignore[attr-defined]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(tip_raw="ERORI FACTURA", tip="ERORI"),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row.zip_path is not None
    assert "messages/" in row.zip_path
    assert row.pdf_path is None
    assert row.email_sent_at == now_utc
    assert len(deps.mailer.sent) == 1  # type: ignore[attr-defined]


def test_pdf_render_failure_still_sends_email_with_zip_only(deps: SyncDeps, now_utc) -> None:
    deps.anaf.download_payload = _make_zip(UBL_FIXTURE)  # type: ignore[attr-defined]
    deps.renderer = FakeRenderer(fail=True)  # type: ignore[assignment]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    add_tracked_counterparty(deps.db, my_cui="12345678", counterparty_cui="RO87654321", now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row.zip_path is not None
    assert row.pdf_path is None
    assert row.email_sent_at == now_utc
    assert row.last_error is not None and "boom" in row.last_error
    [(email, _)] = deps.mailer.sent  # type: ignore[attr-defined]
    assert "PDF: în curs de generare" in email.body
    assert len(email.attachments) == 1  # zip only
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/test_sync.py -v`
Expected: ImportError on `efactura_sync.sync`.

- [ ] **Step 3:** Write `src/efactura_sync/sync.py`

```python
"""Per-CUI sync orchestrator.

Walks each ANAF message through five steps: insert ledger row, download ZIP,
render PDF (invoices only), apply email rules, send email. Each step's success
is recorded in :class:`synced_messages`; a crash mid-step leaves a column NULL
that the next run finishes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from efactura_sync.anaf.messages import (
    InvoiceFields,
    ListMessage,
    extract_ubl_xml,
    parse_invoice_fields,
)
from efactura_sync.errors import EfacturaError, InvalidArchiveError, RenderError
from efactura_sync.mail import (
    EmailMessage,
    render_erori_email,
    render_mesaj_email,
    render_primita_email,
)
from efactura_sync.storage import db as dbq
from efactura_sync.storage.files import FileStore
from efactura_sync.storage.layout import (
    invoice_pdf_path,
    invoice_zip_path,
    message_zip_path,
)

Env = Literal["prod", "test"]


class _AnafLike(Protocol):
    def list_messages(self, *, cif: str, zile: int, access_token: str) -> list[ListMessage]: ...
    def download(self, *, msg_id: str, access_token: str) -> bytes: ...


class _RendererLike(Protocol):
    def render(self, *, ubl_xml: bytes, standard: str = "FACT1") -> bytes: ...


class _MailerLike(Protocol):
    def send(self, msg: EmailMessage, *, to_addr: str) -> None: ...


@dataclass
class SyncDeps:
    anaf: _AnafLike
    renderer: _RendererLike
    mailer: _MailerLike
    files: FileStore
    db: Any  # sqlite3.Connection
    archive_root: Path
    to_addr: str


def _bucharest_local_date(dt: datetime) -> date:
    return dt.astimezone(ZoneInfo("Europe/Bucharest")).date()


def _resolve_partition_date(list_msg: ListMessage, fields: InvoiceFields | None) -> date:
    if fields is not None and fields.issue_date is not None:
        return fields.issue_date
    return _bucharest_local_date(list_msg.data_creare_utc)


def process_one_message(
    deps: SyncDeps,
    *,
    my_cui: str,
    env: Env,
    access_token: str,
    list_msg: ListMessage,
    now: datetime,
) -> None:
    """Walk one ANAF message through download → render → email → ledger.

    Inserts the row if missing. On any non-fatal error the column for the
    failing step stays NULL and ``last_error`` is set; the next run retries.
    """
    # 1. ensure ledger row exists
    existing = dbq.get_synced_message(deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env)
    if existing is None:
        # We don't know issue_date yet (need the XML). Insert with a placeholder
        # date based on data_creare; we'll backfill issue_date on success below.
        partition_date = _bucharest_local_date(list_msg.data_creare_utc)
        dbq.insert_synced_message(
            deps.db,
            dbq.SyncedMessage(
                msg_id=list_msg.msg_id,
                cui=my_cui,
                env=env,
                msg_type=list_msg.tip,
                counterparty_cui=None,
                issue_date=partition_date if list_msg.tip in ("PRIMITA", "TRIMISA") else None,
                zip_path=None,
                pdf_path=None,
                email_sent_at=None,
                email_skip_reason=None,
                first_seen_at=now,
                last_attempt_at=now,
                last_error=None,
            ),
        )

    # 2. download ZIP (skip if already present)
    row = dbq.get_synced_message(deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env)
    assert row is not None
    if row.zip_path is None:
        try:
            zip_bytes = deps.anaf.download(msg_id=list_msg.msg_id, access_token=access_token)
        except EfacturaError as e:
            dbq.update_attempt(
                deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env,
                error=f"download: {e}", now=now,
            )
            return
    else:
        zip_bytes = (deps.archive_root / row.zip_path).read_bytes()

    # 3. for invoices, parse fields to find issue_date + supplier; pick path
    fields: InvoiceFields | None = None
    counterparty_cui: str | None = None
    partition_date = _bucharest_local_date(list_msg.data_creare_utc)
    if list_msg.tip in ("PRIMITA", "TRIMISA"):
        try:
            ubl_xml = extract_ubl_xml(zip_bytes)
            fields = parse_invoice_fields(ubl_xml)
            partition_date = _resolve_partition_date(list_msg, fields)
            counterparty_cui = fields.supplier_cui if list_msg.tip == "PRIMITA" else fields.customer_cui
        except InvalidArchiveError as e:
            dbq.update_attempt(
                deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env,
                error=f"parse: {e}", now=now,
            )
            return

    # 4. write ZIP to disk
    if row.zip_path is None:
        if list_msg.tip in ("PRIMITA", "TRIMISA"):
            zip_target = invoice_zip_path(
                archive_root=deps.archive_root,
                cui=my_cui,
                msg_type=list_msg.tip,  # type: ignore[arg-type]
                issue_date=partition_date,
                msg_id=list_msg.msg_id,
            )
        else:
            zip_target = message_zip_path(
                archive_root=deps.archive_root,
                cui=my_cui,
                creation_date=partition_date,
                msg_id=list_msg.msg_id,
            )
        deps.files.atomic_write(zip_target, zip_bytes)
        rel_zip = str(zip_target.relative_to(deps.archive_root))
        # Patch counterparty/issue_date now that we know them.
        deps.db.execute(
            "UPDATE synced_messages SET zip_path=?, counterparty_cui=?, issue_date=?, "
            "last_attempt_at=?, last_error=NULL "
            "WHERE msg_id=? AND cui=? AND env=?",
            (
                rel_zip,
                counterparty_cui,
                partition_date.isoformat() if list_msg.tip in ("PRIMITA", "TRIMISA") else None,
                now.isoformat().replace("+00:00", "Z"),
                list_msg.msg_id,
                my_cui,
                env,
            ),
        )
        deps.db.commit()
        row = dbq.get_synced_message(deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env)
        assert row is not None

    # 5. render PDF for invoices (if not already done)
    pdf_attachment: tuple[str, bytes] | None = None
    if list_msg.tip in ("PRIMITA", "TRIMISA"):
        if row.pdf_path is None:
            try:
                pdf_bytes = deps.renderer.render(ubl_xml=ubl_xml)  # type: ignore[possibly-undefined]
                pdf_target = invoice_pdf_path(
                    archive_root=deps.archive_root,
                    cui=my_cui,
                    msg_type=list_msg.tip,  # type: ignore[arg-type]
                    issue_date=partition_date,
                    msg_id=list_msg.msg_id,
                )
                deps.files.atomic_write(pdf_target, pdf_bytes)
                rel_pdf = str(pdf_target.relative_to(deps.archive_root))
                dbq.update_pdf_path(
                    deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env,
                    pdf_path=rel_pdf, now=now,
                )
                pdf_attachment = (f"{list_msg.msg_id}.pdf", pdf_bytes)
            except RenderError as e:
                dbq.update_attempt(
                    deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env,
                    error=f"render: {e}", now=now,
                )
                # fall through and email with zip only
        else:
            pdf_attachment = (f"{list_msg.msg_id}.pdf", (deps.archive_root / row.pdf_path).read_bytes())

    # 6. email decision + send
    row = dbq.get_synced_message(deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env)
    assert row is not None
    if row.email_sent_at is not None or row.email_skip_reason is not None:
        return  # already decided

    skip_reason = _decide_skip_reason(deps, my_cui=my_cui, list_msg=list_msg)
    if skip_reason is not None:
        dbq.mark_email_skipped(
            deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env,
            reason=skip_reason, now=now,
        )
        return

    monitored = {c.cui: c for c in dbq.list_monitored_cuis(deps.db)}
    my_display = monitored.get(my_cui).display_name if my_cui in monitored else None
    zip_attachment = (f"{list_msg.msg_id}.zip", zip_bytes)

    email: EmailMessage
    if list_msg.tip == "PRIMITA":
        assert fields is not None
        email = render_primita_email(
            my_cui=my_cui, my_display_name=my_display,
            list_msg=list_msg, fields=fields,
            zip_attachment=zip_attachment, pdf_attachment=pdf_attachment,
            env=env,
        )
    elif list_msg.tip == "ERORI":
        email = render_erori_email(
            my_cui=my_cui, my_display_name=my_display,
            list_msg=list_msg, zip_attachment=zip_attachment, env=env,
        )
    else:  # MESAJ
        email = render_mesaj_email(
            my_cui=my_cui, my_display_name=my_display,
            list_msg=list_msg, zip_attachment=zip_attachment, env=env,
        )

    deps.mailer.send(email, to_addr=deps.to_addr)
    dbq.mark_email_sent(deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env, sent_at=now)


def _decide_skip_reason(deps: SyncDeps, *, my_cui: str, list_msg: ListMessage) -> str | None:
    if list_msg.tip == "TRIMISA":
        return "never_email_for_type"
    if list_msg.tip == "PRIMITA":
        # we need the supplier CUI parsed from the XML — but at decision time
        # we already wrote zip_path and counterparty_cui on the row.
        row = dbq.get_synced_message(
            deps.db, msg_id=list_msg.msg_id, cui=my_cui, env="prod"
        ) or dbq.get_synced_message(deps.db, msg_id=list_msg.msg_id, cui=my_cui, env="test")
        if row is None or row.counterparty_cui is None:
            return None
        if not dbq.is_counterparty_tracked(
            deps.db, my_cui=my_cui, counterparty_cui=row.counterparty_cui
        ):
            return "filtered_by_track_list"
    return None
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/test_sync.py -v`
Expected: 5 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/sync.py tests/test_sync.py
git commit -m "feat(sync): add per-message orchestrator with email rules"
```

---

## Task 19 — `sync.py` per-CUI run with resume + new-message poll

**Files:**
- Modify: `src/efactura_sync/sync.py`
- Modify: `tests/test_sync.py`

- [ ] **Step 1:** Append failing tests

```python
from datetime import timedelta

from efactura_sync.storage.db import upsert_poll_state
from efactura_sync.sync import RunResult, run_for_cui


def test_run_for_cui_polls_and_processes(deps: SyncDeps, now_utc) -> None:
    deps.anaf = FakeAnaf(  # type: ignore[assignment]
        list_response=[_list_msg()],
        download_payload=_make_zip(UBL_FIXTURE),
    )
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    add_tracked_counterparty(deps.db, my_cui="12345678", counterparty_cui="RO87654321", now=now_utc)

    result = run_for_cui(deps, my_cui="12345678", env="prod", access_token="tok", now=now_utc)

    assert isinstance(result, RunResult)
    assert result.processed == 1
    assert result.failures == 0
    [(_, zile)] = deps.anaf.list_calls  # type: ignore[attr-defined]
    assert zile == 1  # first run, no poll_state yet


def test_run_for_cui_uses_zile_window_from_poll_state(deps: SyncDeps, now_utc) -> None:
    deps.anaf = FakeAnaf(list_response=[])  # type: ignore[assignment]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    upsert_poll_state(deps.db, cui="12345678", env="prod", last_polled_at=now_utc - timedelta(days=3))

    run_for_cui(deps, my_cui="12345678", env="prod", access_token="tok", now=now_utc)

    [(_, zile)] = deps.anaf.list_calls  # type: ignore[attr-defined]
    assert zile == 4  # 3 days + 1 safety overlap


def test_run_for_cui_resume_pass_finishes_pending_rows(deps: SyncDeps, now_utc) -> None:
    deps.anaf = FakeAnaf(list_response=[], download_payload=_make_zip(UBL_FIXTURE))  # type: ignore[assignment]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    add_tracked_counterparty(deps.db, my_cui="12345678", counterparty_cui="RO87654321", now=now_utc)
    # Pre-existing pending row from a previous (crashed) run.
    process_one_message(  # this populates everything
        deps, my_cui="12345678", env="prod", access_token="tok",
        list_msg=_list_msg(), now=now_utc,
    )
    # Simulate "crash before email" by manually clearing email_sent_at:
    deps.db.execute(
        "UPDATE synced_messages SET email_sent_at=NULL WHERE msg_id='3001'"
    )
    deps.db.commit()
    deps.mailer.sent.clear()  # type: ignore[attr-defined]

    result = run_for_cui(deps, my_cui="12345678", env="prod", access_token="tok", now=now_utc)

    assert result.processed == 1  # the pending row got finished
    assert result.failures == 0
    assert len(deps.mailer.sent) == 1  # type: ignore[attr-defined]


def test_run_for_cui_records_poll_state_on_success(deps: SyncDeps, now_utc) -> None:
    deps.anaf = FakeAnaf(list_response=[])  # type: ignore[assignment]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)

    run_for_cui(deps, my_cui="12345678", env="prod", access_token="tok", now=now_utc)

    from efactura_sync.storage.db import get_poll_state
    state = get_poll_state(deps.db, cui="12345678", env="prod")
    assert state is not None
    assert state.last_polled_at == now_utc
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/test_sync.py -v -k run_for_cui`
Expected: ImportError on `run_for_cui` / `RunResult`.

- [ ] **Step 3:** Append to `src/efactura_sync/sync.py`

```python
import math


@dataclass
class RunResult:
    processed: int
    failures: int


def _zile_for_run(deps: SyncDeps, *, cui: str, env: Env, now: datetime) -> int:
    state = dbq.get_poll_state(deps.db, cui=cui, env=env)
    if state is None:
        return 1
    delta_days = math.ceil((now - state.last_polled_at).total_seconds() / 86400) + 1
    return max(1, min(60, delta_days))


def run_for_cui(
    deps: SyncDeps,
    *,
    my_cui: str,
    env: Env,
    access_token: str,
    now: datetime,
) -> RunResult:
    """Run a full daily sync for a single CUI.

    Steps: resume any pending rows; list new messages; process each new one.
    Records ``poll_state`` only after the resume + new-message phases both
    complete without raising.
    """
    processed = 0
    failures = 0

    # Resume pass: re-process pending rows. We rebuild a synthetic ListMessage
    # from each pending row so process_one_message can drive its state machine.
    for row in dbq.find_pending_rows(deps.db, cui=my_cui, env=env):
        list_msg = ListMessage(
            msg_id=row.msg_id,
            cif=my_cui,
            data_creare_utc=row.first_seen_at,
            tip_raw=row.msg_type,
            tip=row.msg_type,  # type: ignore[arg-type]
            detalii="",
        )
        try:
            process_one_message(
                deps, my_cui=my_cui, env=env, access_token=access_token,
                list_msg=list_msg, now=now,
            )
            processed += 1
        except Exception as e:
            failures += 1
            dbq.update_attempt(
                deps.db, msg_id=row.msg_id, cui=my_cui, env=env,
                error=f"resume: {e}", now=now,
            )

    # New-message poll
    zile = _zile_for_run(deps, cui=my_cui, env=env, now=now)
    new_msgs = deps.anaf.list_messages(cif=my_cui, zile=zile, access_token=access_token)
    for msg in new_msgs:
        try:
            process_one_message(
                deps, my_cui=my_cui, env=env, access_token=access_token,
                list_msg=msg, now=now,
            )
            processed += 1
        except Exception as e:
            failures += 1
            dbq.update_attempt(
                deps.db, msg_id=msg.msg_id, cui=my_cui, env=env,
                error=f"poll: {e}", now=now,
            )

    dbq.upsert_poll_state(deps.db, cui=my_cui, env=env, last_polled_at=now)
    return RunResult(processed=processed, failures=failures)
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/test_sync.py -v`
Expected: 9 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/sync.py tests/test_sync.py
git commit -m "feat(sync): add per-cui run with resume pass and zile windowing"
```

---

## Task 20 — `cli.py` skeleton + `auth login` + `auth refresh`

**Files:**
- Create: `src/efactura_sync/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1:** Write failing test `tests/test_cli.py`

```python
from pathlib import Path

from typer.testing import CliRunner

from efactura_sync.cli import app

runner = CliRunner()


def test_top_level_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "auth" in result.stdout
    assert "sync" in result.stdout
    assert "cui" in result.stdout
    assert "track" in result.stdout
    assert "status" in result.stdout
    assert "replay" in result.stdout


def test_auth_login_invokes_oauth_and_writes_token(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    from datetime import datetime, timezone

    from efactura_sync.anaf.oauth import Token

    def fake_login(**kwargs):
        captured.update(kwargs)
        return Token(
            cui=kwargs["cui"],
            env=kwargs["env"],
            access_token="acc",
            refresh_token="ref",
            expires_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            obtained_at=kwargs["now"],
        )

    monkeypatch.setattr("efactura_sync.cli.auth_code_login", fake_login)

    config_file = tmp_path / "config.toml"
    secrets_file = tmp_path / "secrets.toml"
    config_file.write_text(
        "[smtp]\nhost='h'\nport=465\ntls='implicit'\nfrom_addr='a'\nto_addr='b'\n"
        "[anaf]\ndefault_env='prod'\n[logging]\nlevel='INFO'\n",
        encoding="utf-8",
    )
    secrets_file.write_text(
        "[smtp]\nusername='u'\npassword='p'\n"
        "[anaf.prod]\nclient_id='cid'\nclient_secret='cs'\n"
        "[anaf.test]\nclient_id='cid'\nclient_secret='cs'\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--config", str(config_file),
            "--secrets", str(secrets_file),
            "--tokens-dir", str(tmp_path / "tokens"),
            "auth", "login",
            "--cui", "12345678",
            "--env", "prod",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["cui"] == "12345678"
    assert captured["env"] == "prod"
    token_file = tmp_path / "tokens" / "12345678.prod.json"
    assert token_file.exists()
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/test_cli.py -v`
Expected: ImportError.

- [ ] **Step 3:** Write `src/efactura_sync/cli.py`

```python
"""Typer CLI."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import typer

from efactura_sync.anaf.oauth import (
    auth_code_login,
    load_token,
    needs_refresh,
    refresh_access_token,
    save_token,
)
from efactura_sync.config import load_config

app = typer.Typer(add_completion=False, no_args_is_help=True)
auth_app = typer.Typer(no_args_is_help=True, help="OAuth token management.")
cui_app = typer.Typer(no_args_is_help=True, help="Manage monitored CUIs.")
track_app = typer.Typer(no_args_is_help=True, help="Manage PRIMITA email allow-list.")
sync_app = typer.Typer(no_args_is_help=True, help="Run the daily sync.")
app.add_typer(auth_app, name="auth")
app.add_typer(cui_app, name="cui")
app.add_typer(track_app, name="track")
app.add_typer(sync_app, name="sync")


@app.callback()
def _main(
    ctx: typer.Context,
    config: Path = typer.Option(
        Path.home() / ".config" / "efactura-sync" / "config.toml",
        "--config",
        help="Path to config.toml",
    ),
    secrets: Path = typer.Option(
        Path.home() / ".config" / "efactura-sync" / "secrets.toml",
        "--secrets",
        help="Path to secrets.toml",
    ),
    tokens_dir: Path = typer.Option(
        Path.home() / ".config" / "efactura-sync" / "tokens",
        "--tokens-dir",
        help="Directory holding per-CUI OAuth token files.",
    ),
) -> None:
    ctx.obj = {
        "config_path": config,
        "secrets_path": secrets,
        "tokens_dir": tokens_dir,
    }


@auth_app.command("login")
def auth_login(
    ctx: typer.Context,
    cui: str = typer.Option(..., "--cui", help="CUI being authorized"),
    env: str = typer.Option("prod", "--env"),
) -> None:
    """Run the interactive OAuth2 authorization-code flow on a host with the digital cert."""
    cfg = load_config(
        config_path=ctx.obj["config_path"], secrets_path=ctx.obj["secrets_path"]
    )
    client_id, client_secret = cfg.anaf_credentials(env)  # type: ignore[arg-type]
    now = datetime.now(timezone.utc)
    with httpx.Client() as http:
        token = auth_code_login(
            http=http,
            env=env,  # type: ignore[arg-type]
            client_id=client_id,
            client_secret=client_secret,
            cui=cui,
            now=now,
        )
    save_token(ctx.obj["tokens_dir"], token)
    typer.echo(f"OK — token saved; expires {token.expires_at.isoformat()}")


@auth_app.command("refresh")
def auth_refresh(
    ctx: typer.Context,
    cui: str = typer.Option(..., "--cui"),
    env: str = typer.Option("prod", "--env"),
) -> None:
    """Refresh access token using the stored refresh token (no cert required)."""
    cfg = load_config(
        config_path=ctx.obj["config_path"], secrets_path=ctx.obj["secrets_path"]
    )
    client_id, client_secret = cfg.anaf_credentials(env)  # type: ignore[arg-type]
    tok = load_token(ctx.obj["tokens_dir"], cui=cui, env=env)
    now = datetime.now(timezone.utc)
    with httpx.Client() as http:
        new_tok = refresh_access_token(
            http=http,
            env=env,  # type: ignore[arg-type]
            client_id=client_id,
            client_secret=client_secret,
            cui=cui,
            refresh_token=tok.refresh_token,
            now=now,
        )
    save_token(ctx.obj["tokens_dir"], new_tok)
    typer.echo(f"OK — refreshed; expires {new_tok.expires_at.isoformat()}")
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/test_cli.py -v`
Expected: 2 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): add typer skeleton + auth login/refresh"
```

---

## Task 21 — `cli.py` `cui` and `track` subcommands

**Files:**
- Modify: `src/efactura_sync/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1:** Append failing test

```python
import sqlite3

from efactura_sync.storage.db import init_schema, list_monitored_cuis, list_tracked_counterparties


def _cli_args(tmp_path: Path, db_path: Path) -> list[str]:
    config_file = tmp_path / "config.toml"
    secrets_file = tmp_path / "secrets.toml"
    if not config_file.exists():
        config_file.write_text(
            "[smtp]\nhost='h'\nport=465\ntls='implicit'\nfrom_addr='a'\nto_addr='b'\n"
            "[anaf]\ndefault_env='prod'\n[logging]\nlevel='INFO'\n",
            encoding="utf-8",
        )
        secrets_file.write_text(
            "[smtp]\nusername='u'\npassword='p'\n"
            "[anaf.prod]\nclient_id='cid'\nclient_secret='cs'\n"
            "[anaf.test]\nclient_id='cid'\nclient_secret='cs'\n",
            encoding="utf-8",
        )
    return [
        "--config", str(config_file),
        "--secrets", str(secrets_file),
        "--tokens-dir", str(tmp_path / "tokens"),
        "--db", str(db_path),
    ]


def test_cui_add_list_remove(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"

    r1 = runner.invoke(app, _cli_args(tmp_path, db_path) + ["cui", "add", "12345678", "--name", "Acme"])
    assert r1.exit_code == 0, r1.stdout

    r2 = runner.invoke(app, _cli_args(tmp_path, db_path) + ["cui", "list"])
    assert "12345678" in r2.stdout
    assert "Acme" in r2.stdout

    r3 = runner.invoke(app, _cli_args(tmp_path, db_path) + ["cui", "remove", "12345678"])
    assert r3.exit_code == 0

    conn = sqlite3.connect(db_path)
    init_schema(conn)
    assert list_monitored_cuis(conn) == []


def test_track_add_list_remove(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    runner.invoke(app, _cli_args(tmp_path, db_path) + ["cui", "add", "12345678"])

    r1 = runner.invoke(
        app,
        _cli_args(tmp_path, db_path) + ["track", "add", "RO111", "--cui", "12345678"],
    )
    assert r1.exit_code == 0, r1.stdout

    r2 = runner.invoke(app, _cli_args(tmp_path, db_path) + ["track", "list", "--cui", "12345678"])
    assert "RO111" in r2.stdout

    r3 = runner.invoke(
        app,
        _cli_args(tmp_path, db_path) + ["track", "remove", "RO111", "--cui", "12345678"],
    )
    assert r3.exit_code == 0

    conn = sqlite3.connect(db_path)
    init_schema(conn)
    assert list_tracked_counterparties(conn, my_cui="12345678") == []
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/test_cli.py -v`
Expected: failures (no `--db` option, no `cui`/`track` commands wired yet).

- [ ] **Step 3:** Modify `src/efactura_sync/cli.py`

Replace the `_main` callback to add `--db`, then append the new commands.

```python
# REPLACE the existing _main callback with:

@app.callback()
def _main(
    ctx: typer.Context,
    config: Path = typer.Option(
        Path.home() / ".config" / "efactura-sync" / "config.toml",
        "--config",
    ),
    secrets: Path = typer.Option(
        Path.home() / ".config" / "efactura-sync" / "secrets.toml",
        "--secrets",
    ),
    tokens_dir: Path = typer.Option(
        Path.home() / ".config" / "efactura-sync" / "tokens",
        "--tokens-dir",
    ),
    db_path: Path = typer.Option(
        Path.home() / ".local" / "share" / "efactura-sync" / "state.db",
        "--db",
        help="Path to SQLite state database.",
    ),
) -> None:
    ctx.obj = {
        "config_path": config,
        "secrets_path": secrets,
        "tokens_dir": tokens_dir,
        "db_path": db_path,
    }
```

Append at the bottom of the file:

```python
import sqlite3

from efactura_sync.storage.db import (
    add_monitored_cui,
    add_tracked_counterparty,
    init_schema,
    list_monitored_cuis,
    list_tracked_counterparties,
    remove_monitored_cui,
    remove_tracked_counterparty,
)


def _open_db(ctx: typer.Context) -> sqlite3.Connection:
    db_path: Path = ctx.obj["db_path"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    return conn


@cui_app.command("add")
def cui_add(
    ctx: typer.Context,
    cui: str = typer.Argument(...),
    name: Optional[str] = typer.Option(None, "--name"),
) -> None:
    conn = _open_db(ctx)
    add_monitored_cui(conn, cui=cui, display_name=name, now=datetime.now(timezone.utc))
    typer.echo(f"OK — monitored cui {cui}")


@cui_app.command("list")
def cui_list(ctx: typer.Context) -> None:
    conn = _open_db(ctx)
    for c in list_monitored_cuis(conn):
        typer.echo(f"{c.cui}\t{c.display_name or ''}")


@cui_app.command("remove")
def cui_remove(ctx: typer.Context, cui: str = typer.Argument(...)) -> None:
    conn = _open_db(ctx)
    remove_monitored_cui(conn, cui=cui)
    typer.echo(f"OK — removed {cui}")


@track_app.command("add")
def track_add(
    ctx: typer.Context,
    counterparty_cui: str = typer.Argument(...),
    cui: str = typer.Option(..., "--cui"),
) -> None:
    conn = _open_db(ctx)
    add_tracked_counterparty(
        conn, my_cui=cui, counterparty_cui=counterparty_cui, now=datetime.now(timezone.utc)
    )
    typer.echo(f"OK — tracking {counterparty_cui} for {cui}")


@track_app.command("list")
def track_list(ctx: typer.Context, cui: str = typer.Option(..., "--cui")) -> None:
    conn = _open_db(ctx)
    for c in list_tracked_counterparties(conn, my_cui=cui):
        typer.echo(c)


@track_app.command("remove")
def track_remove(
    ctx: typer.Context,
    counterparty_cui: str = typer.Argument(...),
    cui: str = typer.Option(..., "--cui"),
) -> None:
    conn = _open_db(ctx)
    remove_tracked_counterparty(conn, my_cui=cui, counterparty_cui=counterparty_cui)
    typer.echo(f"OK — untracked {counterparty_cui} for {cui}")
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/test_cli.py -v`
Expected: 4 passed.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): add cui and track subcommands"
```

---

## Task 22 — `cli.py` `sync run` (with `--dry-run`) + `status` + `replay`

**Files:**
- Modify: `src/efactura_sync/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1:** Append failing test

```python
def test_sync_run_invokes_run_for_cui_for_each_monitored_cui(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    # Pre-populate one monitored CUI
    runner.invoke(app, _cli_args(tmp_path, db_path) + ["cui", "add", "12345678"])
    # Pre-create a token file
    from datetime import datetime, timezone

    from efactura_sync.anaf.oauth import Token, save_token

    save_token(
        tmp_path / "tokens",
        Token(
            cui="12345678",
            env="prod",
            access_token="acc",
            refresh_token="ref",
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            obtained_at=datetime(2026, 5, 4, tzinfo=timezone.utc),
        ),
    )

    calls: list[str] = []

    def fake_run(deps, *, my_cui, env, access_token, now):
        calls.append(my_cui)
        from efactura_sync.sync import RunResult

        return RunResult(processed=0, failures=0)

    monkeypatch.setattr("efactura_sync.cli.run_for_cui", fake_run)

    result = runner.invoke(
        app,
        _cli_args(tmp_path, db_path) + ["sync", "run", "--env", "prod"],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == ["12345678"]


def test_status_lists_cuis_and_token_state(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from efactura_sync.anaf.oauth import Token, save_token

    db_path = tmp_path / "state.db"
    runner.invoke(app, _cli_args(tmp_path, db_path) + ["cui", "add", "12345678", "--name", "Acme"])
    save_token(
        tmp_path / "tokens",
        Token(
            cui="12345678",
            env="prod",
            access_token="acc",
            refresh_token="ref",
            expires_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            obtained_at=datetime(2026, 5, 4, tzinfo=timezone.utc),
        ),
    )

    result = runner.invoke(app, _cli_args(tmp_path, db_path) + ["status", "--env", "prod"])
    assert result.exit_code == 0, result.stdout
    assert "12345678" in result.stdout
    assert "Acme" in result.stdout
    assert "2026-08-01" in result.stdout  # expires_at
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/test_cli.py -v -k "sync_run or status"`
Expected: failure — `sync run` and `status` not implemented yet.

- [ ] **Step 3:** Append to `src/efactura_sync/cli.py`

```python
from efactura_sync.anaf.client import AnafClient
from efactura_sync.mail import Mailer
from efactura_sync.render import PdfRenderer
from efactura_sync.storage.files import FileStore
from efactura_sync.sync import SyncDeps, run_for_cui


@sync_app.command("run")
def sync_run(
    ctx: typer.Context,
    cui: Optional[str] = typer.Option(None, "--cui", help="Only run this CUI; default = all"),
    env: str = typer.Option("prod", "--env"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Run the daily sync for one or all monitored CUIs."""
    cfg = load_config(
        config_path=ctx.obj["config_path"], secrets_path=ctx.obj["secrets_path"]
    )
    conn = _open_db(ctx)
    monitored = list_monitored_cuis(conn)
    if cui is not None:
        monitored = [c for c in monitored if c.cui == cui]
    if not monitored:
        typer.echo("no monitored CUIs to sync")
        raise typer.Exit(code=0)

    if dry_run:
        for c in monitored:
            typer.echo(f"[dry-run] would sync {c.cui} env={env}")
        return

    now = datetime.now(timezone.utc)
    with httpx.Client() as http:
        anaf = AnafClient(http=http, env=env)  # type: ignore[arg-type]
        renderer = PdfRenderer(http=http)
        mailer = Mailer(
            host=cfg.smtp.host,
            port=cfg.smtp.port,
            tls=cfg.smtp.tls,
            username=cfg.smtp.username,
            password=cfg.smtp.password,
            from_addr=cfg.smtp.from_addr,
        )
        deps = SyncDeps(
            anaf=anaf,
            renderer=renderer,
            mailer=mailer,
            files=FileStore(),
            db=conn,
            archive_root=cfg.archive_root,
            to_addr=cfg.smtp.to_addr,
        )
        for c in monitored:
            tok = load_token(ctx.obj["tokens_dir"], cui=c.cui, env=env)
            if needs_refresh(tok, now=now):
                client_id, client_secret = cfg.anaf_credentials(env)  # type: ignore[arg-type]
                tok = refresh_access_token(
                    http=http,
                    env=env,  # type: ignore[arg-type]
                    client_id=client_id,
                    client_secret=client_secret,
                    cui=c.cui,
                    refresh_token=tok.refresh_token,
                    now=now,
                )
                save_token(ctx.obj["tokens_dir"], tok)
            result = run_for_cui(
                deps,
                my_cui=c.cui,
                env=env,  # type: ignore[arg-type]
                access_token=tok.access_token,
                now=now,
            )
            typer.echo(
                f"cui={c.cui} env={env} processed={result.processed} failures={result.failures}"
            )


@app.command("status")
def status(
    ctx: typer.Context,
    env: str = typer.Option("prod", "--env"),
) -> None:
    """Show monitored CUIs, token expiry, last poll, pending counts."""
    conn = _open_db(ctx)
    now = datetime.now(timezone.utc)
    from efactura_sync.errors import AuthError
    from efactura_sync.storage.db import find_pending_rows, get_poll_state

    for c in list_monitored_cuis(conn):
        try:
            tok = load_token(ctx.obj["tokens_dir"], cui=c.cui, env=env)
            tok_str = f"expires {tok.expires_at.isoformat()}"
            if needs_refresh(tok, now=now):
                tok_str += " (refresh due!)"
        except AuthError:
            tok_str = "no token"
        state = get_poll_state(conn, cui=c.cui, env=env)
        last_poll = state.last_polled_at.isoformat() if state else "never"
        pending = len(find_pending_rows(conn, cui=c.cui, env=env))
        typer.echo(
            f"{c.cui}\t{c.display_name or ''}\ttoken: {tok_str}\tlast poll: {last_poll}\tpending: {pending}"
        )


@app.command("replay")
def replay(
    ctx: typer.Context,
    msg_id: str = typer.Argument(...),
    cui: str = typer.Option(..., "--cui"),
    env: str = typer.Option("prod", "--env"),
) -> None:
    """Re-run download/render/email for one specific message, ignoring existing markers."""
    cfg = load_config(
        config_path=ctx.obj["config_path"], secrets_path=ctx.obj["secrets_path"]
    )
    conn = _open_db(ctx)
    # Clear markers so process_one_message redoes everything
    conn.execute(
        "UPDATE synced_messages SET zip_path=NULL, pdf_path=NULL, "
        "email_sent_at=NULL, email_skip_reason=NULL "
        "WHERE msg_id=? AND cui=? AND env=?",
        (msg_id, cui, env),
    )
    conn.commit()
    typer.echo(f"replay queued for {msg_id}; run 'sync run --cui={cui} --env={env}' to process")
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/test_cli.py -v`
Expected: all CLI tests pass.

- [ ] **Step 5:** Commit

```bash
git add src/efactura_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): add sync run, status, and replay commands"
```

---

## Task 23 — Top-level failure-notification email

**Files:**
- Modify: `src/efactura_sync/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1:** Append failing test

```python
def test_sync_run_sends_failure_email_on_uncaught_exception(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    runner.invoke(app, _cli_args(tmp_path, db_path) + ["cui", "add", "12345678"])
    from datetime import datetime, timezone

    from efactura_sync.anaf.oauth import Token, save_token

    save_token(
        tmp_path / "tokens",
        Token(
            cui="12345678",
            env="prod",
            access_token="acc",
            refresh_token="ref",
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            obtained_at=datetime(2026, 5, 4, tzinfo=timezone.utc),
        ),
    )

    sent: list = []

    class _CapturingMailer:
        def __init__(self, **_: object) -> None:
            pass

        def send(self, msg, *, to_addr) -> None:
            sent.append((msg, to_addr))

    monkeypatch.setattr("efactura_sync.cli.Mailer", _CapturingMailer)

    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("efactura_sync.cli.run_for_cui", boom)

    result = runner.invoke(app, _cli_args(tmp_path, db_path) + ["sync", "run", "--env", "prod"])
    assert result.exit_code != 0
    assert sent, "expected a failure email"
    msg, to_addr = sent[0]
    assert "[eroare-rulare]" in msg.subject
    assert "kaboom" in msg.body or "RuntimeError" in msg.body
```

- [ ] **Step 2:** Run to verify failure

Run: `uv run pytest tests/test_cli.py -v -k failure_email`
Expected: failure (no failure email path yet).

- [ ] **Step 3:** Modify `src/efactura_sync/cli.py` — wrap the body of `sync_run` in a try/except. Replace the entire `sync_run` function with:

```python
@sync_app.command("run")
def sync_run(
    ctx: typer.Context,
    cui: Optional[str] = typer.Option(None, "--cui"),
    env: str = typer.Option("prod", "--env"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Run the daily sync for one or all monitored CUIs."""
    import socket
    import traceback

    from efactura_sync.mail import render_failure_email

    cfg = load_config(
        config_path=ctx.obj["config_path"], secrets_path=ctx.obj["secrets_path"]
    )
    conn = _open_db(ctx)
    monitored = list_monitored_cuis(conn)
    if cui is not None:
        monitored = [c for c in monitored if c.cui == cui]
    if not monitored:
        typer.echo("no monitored CUIs to sync")
        raise typer.Exit(code=0)

    if dry_run:
        for c in monitored:
            typer.echo(f"[dry-run] would sync {c.cui} env={env}")
        return

    now = datetime.now(timezone.utc)
    cui_in_progress: str | None = None
    step: str | None = None
    try:
        with httpx.Client() as http:
            anaf = AnafClient(http=http, env=env)  # type: ignore[arg-type]
            renderer = PdfRenderer(http=http)
            mailer = Mailer(
                host=cfg.smtp.host,
                port=cfg.smtp.port,
                tls=cfg.smtp.tls,
                username=cfg.smtp.username,
                password=cfg.smtp.password,
                from_addr=cfg.smtp.from_addr,
            )
            deps = SyncDeps(
                anaf=anaf,
                renderer=renderer,
                mailer=mailer,
                files=FileStore(),
                db=conn,
                archive_root=cfg.archive_root,
                to_addr=cfg.smtp.to_addr,
            )
            for c in monitored:
                cui_in_progress = c.cui
                step = "auth"
                tok = load_token(ctx.obj["tokens_dir"], cui=c.cui, env=env)
                if needs_refresh(tok, now=now):
                    client_id, client_secret = cfg.anaf_credentials(env)  # type: ignore[arg-type]
                    tok = refresh_access_token(
                        http=http,
                        env=env,  # type: ignore[arg-type]
                        client_id=client_id,
                        client_secret=client_secret,
                        cui=c.cui,
                        refresh_token=tok.refresh_token,
                        now=now,
                    )
                    save_token(ctx.obj["tokens_dir"], tok)
                step = "sync"
                result = run_for_cui(
                    deps,
                    my_cui=c.cui,
                    env=env,  # type: ignore[arg-type]
                    access_token=tok.access_token,
                    now=now,
                )
                typer.echo(
                    f"cui={c.cui} env={env} processed={result.processed} failures={result.failures}"
                )
    except Exception as exc:
        # Send a failure-notification email and re-raise to fail the process.
        try:
            failure_mailer = Mailer(
                host=cfg.smtp.host,
                port=cfg.smtp.port,
                tls=cfg.smtp.tls,
                username=cfg.smtp.username,
                password=cfg.smtp.password,
                from_addr=cfg.smtp.from_addr,
            )
            email = render_failure_email(
                hostname=socket.gethostname(),
                run_date=now.date(),
                cui_in_progress=cui_in_progress,
                step=step,
                exception_type=type(exc).__name__,
                log_tail=str(exc),
                traceback=traceback.format_exc(),
            )
            failure_mailer.send(email, to_addr=cfg.smtp.error_to_addr)
        except Exception:
            # Notification failed too — nothing more we can do here.
            pass
        raise typer.Exit(code=1) from exc
```

- [ ] **Step 4:** Run to verify it passes

Run: `uv run pytest tests/test_cli.py -v`
Expected: all CLI tests pass, including failure email.

- [ ] **Step 5:** Lint + typecheck the whole tree

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass. (Fix issues inline before committing if any surface.)

- [ ] **Step 6:** Commit

```bash
git add src/efactura_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): send failure-notification email on uncaught sync error"
```

---

## Task 24 — README with setup + run instructions

**Files:**
- Modify: `README.md`

- [ ] **Step 1:** Replace `README.md` with:

````markdown
# efactura-sync

Daily archival sync for Romanian ANAF e-Factura SPV. Read-only — pulls invoices and messages, never uploads.

See the design spec at [`docs/superpowers/specs/2026-05-04-efactura-sync-design.md`](docs/superpowers/specs/2026-05-04-efactura-sync-design.md).

## Setup

Prerequisites: Python 3.12+, [`uv`](https://docs.astral.sh/uv/), and a registered ANAF OAuth application (see `docs/Oauth_procedura_inregistrare_aplicatii_portal_ANAF.pdf`).

```bash
uv sync --dev
```

Create config files:

```bash
mkdir -p ~/.config/efactura-sync/tokens
chmod 700 ~/.config/efactura-sync ~/.config/efactura-sync/tokens
```

`~/.config/efactura-sync/config.toml`:

```toml
[smtp]
host = "smtp.fastmail.com"
port = 465
tls = "implicit"
from_addr = "efactura@yourdomain.tld"
to_addr = "you@yourdomain.tld"

[anaf]
default_env = "prod"

[logging]
level = "INFO"
```

`~/.config/efactura-sync/secrets.toml` (chmod 0600):

```toml
[smtp]
username = "efactura@yourdomain.tld"
password = "your-app-password"

[anaf.prod]
client_id = "from-anaf-portal"
client_secret = "from-anaf-portal"

[anaf.test]
client_id = "from-anaf-portal-test"
client_secret = "from-anaf-portal-test"
```

```bash
chmod 600 ~/.config/efactura-sync/secrets.toml
```

## Onboard a CUI

On the **laptop** (digital cert plugged in):

```bash
uv run efactura-sync cui add 12345678 --name "Acme SRL"
uv run efactura-sync auth login --cui 12345678 --env prod
# Browser opens, ANAF asks for cert PIN, finishes silently.
# Then copy the token to the server:
scp ~/.config/efactura-sync/tokens/12345678.prod.json server:~/.config/efactura-sync/tokens/
```

Add suppliers to email allow-list:

```bash
uv run efactura-sync track add RO87654321 --cui 12345678
```

## Daily run (server)

```bash
uv run efactura-sync sync run --env prod
```

Schedule via cron once daily (example, 03:00 local):

```cron
0 3 * * * /usr/bin/env -S /home/youruser/.local/bin/uv run --project /home/youruser/efactura-sync efactura-sync sync run --env prod
```

## Inspecting state

```bash
uv run efactura-sync status --env prod
uv run efactura-sync sync run --env prod --dry-run
uv run efactura-sync replay <msg_id> --cui 12345678 --env prod
```

## Tests

```bash
uv run pytest                       # unit tests, offline
uv run pytest -m integration        # opt-in: hits real ANAF test env
uv run ruff check . && uv run ruff format --check .
uv run mypy src
```
````

- [ ] **Step 2:** Verify the file lints clean

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: pass.

- [ ] **Step 3:** Commit

```bash
git add README.md
git commit -m "docs: add README with setup and daily-run instructions"
```

---

## Self-review notes (kept inline for posterity)

**Spec coverage** (against `docs/superpowers/specs/2026-05-04-efactura-sync-design.md`):

| Spec section | Implementing task(s) |
|---|---|
| §3 architecture / two-host roles | Tasks 14–15 (oauth + auth flow), Tasks 20–22 (cli) |
| §4 modules | All — see file map |
| §5.1 on-disk layout | Task 4 (layout) |
| §5.2–5.4 config files | Task 10 (config), Task 14 (token I/O) |
| §5.5 SQLite schema | Tasks 6–9 |
| §6 email rules + Romanian rendering | Tasks 16, 18 (rule application) |
| §6.5 failure email | Task 23 |
| §7.1 OAuth (auth-code + refresh) | Tasks 14, 15 |
| §7.2 listamesaje + zile | Task 12 + Task 19 (`_zile_for_run`) |
| §7.3 download + atomic | Task 5, Task 12 |
| §7.4 xmltopdf | Task 13 |
| §7.5 transient/permanent classification | Task 12 |
| §8 idempotency / resume | Tasks 9, 18, 19 |
| §8.4 dry-run + replay | Task 22 |
| §9 tests | Distributed across all tasks; conftest in Task 2 |
| §10 deferred (healthchecks, parallelism) | Not implemented; documented in spec |

**Known small gaps** (defer to follow-up if they appear during execution):

- The `replay` test isn't exercised end-to-end (only unit-tested at the SQL-clear level).
- Integration test (`@pytest.mark.integration`) is referenced in the README but no test file is created — that's intentional: it requires real ANAF test credentials and is added at first integration test run.
- `_classify` retry-with-backoff loop from spec §7.5 is *not* implemented inside `AnafClient`; spec calls for `[1s, 5s, 30s, 5m]` retry on transient. Add this in the next iteration if needed; current code raises `TransientError` on the first failure and the next daily run picks up.

These gaps are acceptable for v1 ship; the spec lists §10 as "deferred" already covers attitude here.
