# Application Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up a central, config-driven logging configuration and emit INFO (milestones / external side-effects) and DEBUG (details) log lines at every action site in the app.

**Architecture:** A new `logging_setup.setup_logging(level)` configures the `efactura_sync` package logger (single stderr handler, human-readable format). A cheap `config.read_log_level(config_path)` reads only `[logging].level` so the Typer `@app.callback` can apply the configured level to every command without forcing `config.toml` to exist for DB-only commands. Each module then gets a module logger and log statements following a fixed INFO/DEBUG policy.

**Tech Stack:** Python 3.14, stdlib `logging`, Typer, pytest (`caplog`), ruff, mypy (strict).

**Spec:** `docs/superpowers/specs/2026-06-04-application-logging-design.md`

---

## Conventions for every task

- Every module that logs uses, at the top after imports: `_log = logging.getLogger(__name__)`.
- Logging is **additive**: never change control flow, exit codes, or `typer.echo` output.
- **Never log secrets**: no access/refresh tokens, client secrets, or SMTP passwords. Log only metadata (cui, env, host, expiry, byte counts, status codes, msg ids).
- Use `%`-style lazy args (`_log.info("x=%s", x)`), matching the existing code in `sync.py`.
- Commit messages: end with the project's `Co-Authored-By` trailer. If GPG signing fails in a non-interactive shell, commit with `--no-gpg-sign` and note it.
- Run `git add` with the exact files listed in each task's **Files** block.

---

## Task 1: `logging_setup` module

**Files:**
- Create: `src/efactura_sync/logging_setup.py`
- Test: `tests/test_logging_setup.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_logging_setup.py
import logging

from efactura_sync.logging_setup import setup_logging

_PKG = "efactura_sync"


def _reset_pkg_logger() -> None:
    logger = logging.getLogger(_PKG)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    # reset the module-level idempotency guard
    import efactura_sync.logging_setup as ls

    ls._configured = False


def test_setup_logging_sets_level_and_one_handler() -> None:
    _reset_pkg_logger()
    setup_logging("DEBUG")
    logger = logging.getLogger(_PKG)
    assert logger.level == logging.DEBUG
    assert len(logger.handlers) == 1
    assert logger.propagate is False


def test_setup_logging_idempotent_no_duplicate_handler() -> None:
    _reset_pkg_logger()
    setup_logging("DEBUG")
    setup_logging("INFO")
    logger = logging.getLogger(_PKG)
    assert logger.level == logging.INFO
    assert len(logger.handlers) == 1


def test_setup_logging_unknown_level_falls_back_to_info() -> None:
    _reset_pkg_logger()
    setup_logging("garbage")
    logger = logging.getLogger(_PKG)
    assert logger.level == logging.INFO


def test_emitted_record_is_captured_at_level(caplog) -> None:
    _reset_pkg_logger()
    setup_logging("INFO")
    with caplog.at_level(logging.INFO, logger="efactura_sync.sample"):
        logging.getLogger("efactura_sync.sample").info("hello %d", 1)
    assert "hello 1" in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_logging_setup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'efactura_sync.logging_setup'`.

- [ ] **Step 3: Write the implementation**

```python
# src/efactura_sync/logging_setup.py
"""Central logging configuration for the efactura-sync CLI.

Configures the ``efactura_sync`` package logger only (not the root logger), so
third-party libraries (httpx, smtplib) are never configured by us. Logs go to
stderr; stdout is reserved for ``typer.echo`` CLI output.
"""

import logging
import sys

_PACKAGE_LOGGER = "efactura_sync"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: str) -> None:
    """Configure the ``efactura_sync`` package logger. Idempotent.

    Attaches a single ``StreamHandler(sys.stderr)`` with a human-readable
    formatter, sets the level, and disables propagation. A second call updates
    the level without adding a duplicate handler. Unknown level strings fall
    back to INFO with a warning.
    """
    logger = logging.getLogger(_PACKAGE_LOGGER)
    numeric = logging.getLevelName(level.upper())
    bad: str | None = None
    if not isinstance(numeric, int):
        numeric = logging.INFO
        bad = level

    logger.setLevel(numeric)
    logger.propagate = False

    global _configured
    if not _configured:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        logger.addHandler(handler)
        _configured = True

    if bad is not None:
        logger.warning("unknown log level %r; falling back to INFO", bad)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_logging_setup.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/efactura_sync/logging_setup.py tests/test_logging_setup.py && uv run mypy src/efactura_sync/logging_setup.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/efactura_sync/logging_setup.py tests/test_logging_setup.py
git commit -m "feat(logging): central setup_logging for the package logger

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `read_log_level` helper in config

**Files:**
- Modify: `src/efactura_sync/config.py` (add `read_log_level`)
- Test: `tests/test_config.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
from efactura_sync.config import read_log_level


def test_read_log_level_returns_configured_value(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        "config.toml",
        """
        [logging]
        level = "DEBUG"
        """,
    )
    assert read_log_level(cfg) == "DEBUG"


def test_read_log_level_missing_file_defaults_info(tmp_path: Path) -> None:
    assert read_log_level(tmp_path / "nope.toml") == "INFO"


def test_read_log_level_no_logging_section_defaults_info(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "config.toml", "[smtp]\nhost = \"x\"\n")
    assert read_log_level(cfg) == "INFO"


def test_read_log_level_malformed_toml_defaults_info(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "config.toml", "this is = = not toml [[[")
    assert read_log_level(cfg) == "INFO"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k read_log_level -v`
Expected: FAIL — `ImportError: cannot import name 'read_log_level'`.

- [ ] **Step 3: Write the implementation**

Add to `src/efactura_sync/config.py` (after `load_config`):

```python
def read_log_level(config_path: Path) -> str:
    """Read ``[logging].level`` from config.toml; return "INFO" on any problem.

    Used by the CLI to configure logging before full config validation, so the
    level applies even to commands that do not otherwise load config. Never
    raises: missing file, malformed TOML, or a missing section all yield "INFO".
    """
    try:
        cfg = _read_toml(config_path, label="config.toml")
    except ConfigError:
        return "INFO"
    log_cfg = cfg.get("logging")
    if not isinstance(log_cfg, dict):
        return "INFO"
    return str(log_cfg.get("level", "INFO"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -k read_log_level -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/efactura_sync/config.py tests/test_config.py && uv run mypy src/efactura_sync/config.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/efactura_sync/config.py tests/test_config.py
git commit -m "feat(config): read_log_level helper for cheap level lookup

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Wire up logging in the CLI callback + command logs

**Files:**
- Modify: `src/efactura_sync/cli.py`
- Test: `tests/test_cli.py` (append one test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` (uses Typer's `CliRunner`; match how the file already builds its runner — reuse the existing fixture/imports there). This asserts that running a command configures the package logger:

```python
import logging

from typer.testing import CliRunner

from efactura_sync.cli import app


def test_callback_configures_package_logger(tmp_path, monkeypatch) -> None:
    # Point config resolution at an empty dir so read_log_level falls back to INFO.
    monkeypatch.setenv("EFACTURA_SYNC_HOME", str(tmp_path))  # adjust to the env var resolve_paths uses
    # reset guard so setup_logging re-attaches in isolation
    import efactura_sync.logging_setup as ls

    for h in list(logging.getLogger("efactura_sync").handlers):
        logging.getLogger("efactura_sync").removeHandler(h)
    ls._configured = False

    runner = CliRunner()
    result = runner.invoke(app, ["cui", "list"])
    assert result.exit_code == 0
    logger = logging.getLogger("efactura_sync")
    assert len(logger.handlers) == 1
    assert logger.propagate is False
```

> Note for the implementer: check `tests/test_cli.py` for the existing pattern that sets up `resolve_paths()` (env var name + temp dirs) and reuse it verbatim instead of the placeholder `EFACTURA_SYNC_HOME` above. If a shared fixture exists, depend on it.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -k callback_configures -v`
Expected: FAIL — the `efactura_sync` logger has 0 handlers (callback does not configure logging yet).

- [ ] **Step 3: Implement the wire-up + command logs**

In `src/efactura_sync/cli.py`:

3a. Add imports near the existing ones:

```python
import logging
```

and extend the config import:

```python
from efactura_sync.config import Config, load_config, read_log_level
from efactura_sync.logging_setup import setup_logging
```

3b. Add a module logger after `app = typer.Typer(...)` and the sub-apps:

```python
_log = logging.getLogger(__name__)
```

3c. Configure logging at the end of the callback (after paths resolve):

```python
@app.callback()
def _main(ctx: typer.Context) -> None:
    try:
        ctx.obj = resolve_paths()
    except PathConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    setup_logging(read_log_level(ctx.obj.config_path))
```

3d. Add a DEBUG line at `_open_db` return:

```python
def _open_db(ctx: typer.Context) -> sqlite3.Connection:
    """Open the state DB, ensuring schema + foreign keys are on."""
    cli_ctx = _ctx(ctx)
    cli_ctx.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _db_connect(cli_ctx.db_path)
    init_schema(conn)
    _log.debug("db opened path=%s", cli_ctx.db_path)
    return conn
```

3e. Add DEBUG command-invocation lines as the first statement in each command body, and an INFO line on the token refresh inside `sync run`. Examples (add the analogous one-liner to every command):

```python
# auth_login
_log.debug("auth login cui=%s env=%s browser=%s", cui, env, browser)
# auth_refresh
_log.debug("auth refresh cui=%s env=%s", cui, env)
# cui_add_cmd
_log.debug("cui add cui=%s name=%s", cui, name)
# cui_list_cmd
_log.debug("cui list")
# cui_remove_cmd
_log.debug("cui remove cui=%s", cui)
# watch_add_cmd
_log.debug("watch add counterparty=%s cui=%s", counterparty_cui, cui)
# watch_list_cmd
_log.debug("watch list cui=%s", cui)
# watch_remove_cmd
_log.debug("watch remove counterparty=%s cui=%s", counterparty_cui, cui)
# status_cmd
_log.debug("status env=%s", env)
# replay_cmd
_log.debug("replay msg_id=%s cui=%s env=%s", msg_id, cui, env)
# sync_run_cmd (after _prepare)
_log.debug("sync run cui=%s env=%s dry_run=%s zile=%s", cui, env, dry_run, zile)
```

In `sync_run_cmd`, inside the `if needs_refresh(tok, now=now):` block (just before or after `save_token`), add:

```python
_log.info("token refresh cui=%s env=%s", m.cui, env_typed)
```

- [ ] **Step 4: Run the test + full CLI suite to verify pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS — the new test passes and all existing CLI tests still pass (logging is on stderr; stdout assertions unaffected).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/efactura_sync/cli.py tests/test_cli.py && uv run mypy src/efactura_sync/cli.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/efactura_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): configure logging in callback; per-command debug logs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: ANAF HTTP client logs

**Files:**
- Modify: `src/efactura_sync/anaf/client.py`
- Test: `tests/anaf/` (append a caplog smoke test to the existing client test file; if none exists, add `tests/anaf/test_client.py`)

- [ ] **Step 1: Write the failing test**

First check for an existing client test: `ls tests/anaf/`. Append to the client test module (or create `tests/anaf/test_client.py` mirroring the existing fakes/style):

```python
import logging

import httpx

from efactura_sync.anaf.client import AnafClient


def test_list_messages_logs_summary(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"mesaje": []})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http:
        client = AnafClient(http=http, env="test")
        with caplog.at_level(logging.INFO, logger="efactura_sync.anaf.client"):
            client.list_messages(cif="123", zile=7, access_token="tok")
    assert any("list_messages" in r.message and "cif=123" in r.message for r in caplog.records)
    # the token must never be logged
    assert "tok" not in caplog.text
```

> Implementer: confirm the empty-list ANAF payload shape `parse_list_response` expects (look at `anaf/messages.py::parse_list_response`) and adjust the mock JSON if `{"mesaje": []}` is not the empty form. The assertion that matters is the INFO summary line + no token leak.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/anaf -k list_messages_logs -v`
Expected: FAIL — no `list_messages` INFO record present.

- [ ] **Step 3: Implement logs**

In `src/efactura_sync/anaf/client.py`, add `import logging` and `_log = logging.getLogger(__name__)` after imports. Then:

```python
def list_messages(self, *, cif: str, zile: int, access_token: str) -> list[ListMessage]:
    zile_clamped = max(1, min(60, zile))
    _log.debug("GET listaMesajeFactura cif=%s zile=%d", cif, zile_clamped)
    resp = self._http.get(
        f"{self._base}/listaMesajeFactura",
        params={"cif": cif, "zile": zile_clamped},
        headers=self._headers(access_token),
        timeout=30.0,
    )
    _log.debug("listaMesajeFactura -> HTTP %d (%d bytes)", resp.status_code, len(resp.content))
    _classify(resp)
    # ... unchanged JSON parsing ...
    messages = parse_list_response(payload)
    _log.info("list_messages cif=%s zile=%d -> %d messages", cif, zile_clamped, len(messages))
    return messages
```

(Replace the existing `return parse_list_response(payload)` with the two lines above.)

```python
def download(self, *, msg_id: str, access_token: str) -> bytes:
    _log.debug("GET descarcare id=%s", msg_id)
    resp = self._http.get(
        f"{self._base}/descarcare",
        params={"id": msg_id},
        headers=self._headers(access_token),
        timeout=60.0,
    )
    _classify(resp)
    _log.info("download msg_id=%s -> %d bytes", msg_id, len(resp.content))
    return resp.content
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/anaf -v`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/efactura_sync/anaf/client.py && uv run mypy src/efactura_sync/anaf/client.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/efactura_sync/anaf/client.py tests/anaf
git commit -m "feat(anaf): log HTTP list/download calls (no token leak)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: OAuth logs (metadata only)

**Files:**
- Modify: `src/efactura_sync/anaf/oauth.py`
- Test: `tests/anaf/` (append a caplog test to the oauth test module; check `ls tests/anaf/` for the existing file)

- [ ] **Step 1: Write the failing test**

Append to the existing oauth test module (mirror its fakes). The key assertions: a refresh logs an INFO success line with `cui=` and `expires_at=`, and **no token value appears**:

```python
import logging

import httpx

from efactura_sync.anaf.oauth import refresh_access_token


def test_refresh_logs_metadata_not_token(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"access_token": "SECRET_AT", "refresh_token": "SECRET_RT", "expires_in": 3600},
        )

    from datetime import UTC, datetime

    now = datetime(2026, 6, 4, tzinfo=UTC)
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http:
        with caplog.at_level(logging.DEBUG, logger="efactura_sync.anaf.oauth"):
            refresh_access_token(
                http=http, env="test", client_id="id", client_secret="sec",
                cui="123", refresh_token="OLD_RT", now=now,
            )
    assert any("oauth refresh ok" in r.message and "cui=123" in r.message for r in caplog.records)
    assert "SECRET_AT" not in caplog.text
    assert "SECRET_RT" not in caplog.text
    assert "OLD_RT" not in caplog.text
    assert "sec" not in caplog.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/anaf -k refresh_logs_metadata -v`
Expected: FAIL — no `oauth refresh ok` record.

- [ ] **Step 3: Implement logs**

In `src/efactura_sync/anaf/oauth.py`, add `import logging` and `_log = logging.getLogger(__name__)` after imports. Add:

```python
# in save_token, before/after os.replace(partial, p):
_log.debug("token saved cui=%s env=%s", token.cui, token.env)

# in load_token, just before the final return Token(...):
_log.debug("token loaded cui=%s env=%s", cui, env)

# in exchange_code, replace the final return with:
token = _token_from_body(resp.json(), cui=cui, env=env, now=now)
_log.info("oauth exchange ok cui=%s env=%s expires_at=%s", cui, env, token.expires_at.isoformat())
return token

# in refresh_access_token, add before the POST:
_log.debug("oauth refresh start cui=%s env=%s", cui, env)
# ...and replace the final return with:
token = _token_from_body(
    resp.json(), cui=cui, env=env, now=now, fallback_refresh_token=refresh_token
)
_log.info("oauth refresh ok cui=%s expires_at=%s", cui, token.expires_at.isoformat())
return token
```

(For `save_token`, place the debug line after `os.replace(partial, p)` so it logs only on success.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/anaf -v`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/efactura_sync/anaf/oauth.py && uv run mypy src/efactura_sync/anaf/oauth.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/efactura_sync/anaf/oauth.py tests/anaf
git commit -m "feat(oauth): log exchange/refresh metadata (never tokens)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: PDF renderer logs

**Files:**
- Modify: `src/efactura_sync/render.py`
- Test: `tests/test_render.py` (append a caplog test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_render.py` (reuse the file's existing httpx mock pattern):

```python
import logging

import httpx

from efactura_sync.render import PdfRenderer


def test_render_logs_success(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF-1.7 ...")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http:
        r = PdfRenderer(http=http, env="test")
        with caplog.at_level(logging.INFO, logger="efactura_sync.render"):
            r.render(ubl_xml=b"<Invoice/>")
    assert any("rendered PDF" in rec.message for rec in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_render.py -k render_logs_success -v`
Expected: FAIL — no `rendered PDF` record.

- [ ] **Step 3: Implement logs**

In `src/efactura_sync/render.py`, add `import logging` and `_log = logging.getLogger(__name__)`. In `render`:

```python
def render(self, *, ubl_xml: bytes, standard: str = "FACT1") -> bytes:
    _log.debug("xmltopdf standard=%s xml_bytes=%d", standard, len(ubl_xml))
    resp = self._http.post(
        f"{self._base}/{standard}",
        content=ubl_xml,
        headers={"Content-Type": "text/plain", "User-Agent": USER_AGENT},
        timeout=30.0,
    )
    if resp.status_code != 200:
        snippet = resp.content[:200].decode("utf-8", "replace")
        raise RenderError(f"xmltopdf returned HTTP {resp.status_code}: {snippet}")
    if not resp.content.startswith(b"%PDF"):
        raise RenderError("xmltopdf response is not a PDF")
    _log.info("rendered PDF %d bytes", len(resp.content))
    return resp.content
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_render.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/efactura_sync/render.py && uv run mypy src/efactura_sync/render.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/efactura_sync/render.py tests/test_render.py
git commit -m "feat(render): log xmltopdf request/result

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Mailer logs

**Files:**
- Modify: `src/efactura_sync/mail.py`
- Test: `tests/test_mail.py` (append a caplog test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mail.py` (reuse its existing SMTP fake/monkeypatch pattern — check how `test_mail.py` already stubs `smtplib`). Goal: a successful `send` logs an INFO line containing `email sent` and the subject, and the password never appears:

```python
import logging


def test_send_logs_success(monkeypatch, caplog) -> None:
    # Reuse the existing fake SMTP from this module's other tests.
    # (Replace FakeSMTP / wiring with whatever this file already defines.)
    ...  # construct Mailer with a fake transport exactly as the existing send test does
    with caplog.at_level(logging.INFO, logger="efactura_sync.mail"):
        mailer.send(message, to_addr="to@example.com")
    assert any("email sent" in r.message for r in caplog.records)
    assert "secretpassword" not in caplog.text
```

> Implementer: model this test on the existing successful-send test in `tests/test_mail.py`; do not invent a new SMTP fake if one already exists.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mail.py -k send_logs_success -v`
Expected: FAIL — no `email sent` record.

- [ ] **Step 3: Implement logs**

In `src/efactura_sync/mail.py`, add `import logging` and `_log = logging.getLogger(__name__)`. In `Mailer.send`, add a DEBUG connect line and an INFO success line (password is never referenced in the message):

```python
def send(self, msg: EmailMessage, *, to_addr: str) -> None:
    std = _StdlibEmailMessage()
    # ... unchanged message construction ...
    _log.debug("smtp connect host=%s port=%d tls=%s", self._host, self._port, self._tls)
    with self._connect() as smtp:
        if self._tls == "starttls":
            smtp.starttls()
        smtp.login(self._username, self._password)
        smtp.sendmail(self._from, [to_addr], std.as_bytes())
    _log.info("email sent subject=%r to=%s", msg.subject, to_addr)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mail.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/efactura_sync/mail.py && uv run mypy src/efactura_sync/mail.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/efactura_sync/mail.py tests/test_mail.py
git commit -m "feat(mail): log smtp connect + email-sent (no password)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: File store logs

**Files:**
- Modify: `src/efactura_sync/storage/files.py`
- Test: `tests/storage/` (append a caplog test; check `ls tests/storage/` for the files test module)

- [ ] **Step 1: Write the failing test**

Append to the storage files test module (or create `tests/storage/test_files.py`):

```python
import logging
from pathlib import Path

from efactura_sync.storage.files import FileStore


def test_atomic_write_logs_debug(tmp_path: Path, caplog) -> None:
    target = tmp_path / "sub" / "x.bin"
    with caplog.at_level(logging.DEBUG, logger="efactura_sync.storage.files"):
        FileStore().atomic_write(target, b"hello")
    assert target.read_bytes() == b"hello"
    assert any("atomic_write" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/storage -k atomic_write_logs -v`
Expected: FAIL — no `atomic_write` record.

- [ ] **Step 3: Implement logs**

In `src/efactura_sync/storage/files.py`, add `import logging` and `_log = logging.getLogger(__name__)`. At the end of `atomic_write` is tricky (it has early `return` paths), so log right after the durable rename succeeds — place the debug line immediately after `os.replace(partial, target)`:

```python
os.replace(partial, target)
_log.debug("atomic_write %s (%d bytes)", target, len(data))
```

And in `sweep_partials`, before `return removed`:

```python
if removed:
    _log.debug("swept %d stale .partial file(s)", len(removed))
return removed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/storage -v`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/efactura_sync/storage/files.py && uv run mypy src/efactura_sync/storage/files.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/efactura_sync/storage/files.py tests/storage
git commit -m "feat(storage): debug-log atomic writes and partial sweep

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: DB mutation + schema logs

**Files:**
- Modify: `src/efactura_sync/storage/db.py`
- Test: `tests/storage/` (append a caplog test for one mutation)

- [ ] **Step 1: Write the failing test**

Append to the storage db test module (uses the in-memory `db` fixture from `tests/conftest.py`):

```python
import logging
from datetime import UTC, datetime

from efactura_sync.storage.db import add_monitored_cui, init_schema


def test_add_monitored_cui_logs_debug(db, caplog) -> None:
    init_schema(db)
    with caplog.at_level(logging.DEBUG, logger="efactura_sync.storage.db"):
        add_monitored_cui(db, cui="123", display_name="Acme", now=datetime(2026, 6, 4, tzinfo=UTC))
    assert any("monitored cui added" in r.message and "123" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/storage -k add_monitored_cui_logs -v`
Expected: FAIL — no record.

- [ ] **Step 3: Implement logs**

In `src/efactura_sync/storage/db.py`, add `import logging` and `_log = logging.getLogger(__name__)`. Add a `DEBUG` line at the end of each mutating helper, and an INFO line in `init_schema`. Add these (place each before the function returns / after its `conn.commit()` where present):

```python
# init_schema — after the schema DDL/commit:
_log.debug("schema initialized")

# add_monitored_cui — after commit:
_log.debug("monitored cui added cui=%s name=%s", cui, display_name)
# remove_monitored_cui:
_log.debug("monitored cui removed cui=%s", cui)
# add_watched_counterparty:
_log.debug("watch added my_cui=%s counterparty=%s", my_cui, counterparty_cui)
# remove_watched_counterparty:
_log.debug("watch removed my_cui=%s counterparty=%s", my_cui, counterparty_cui)
# upsert_poll_state:
_log.debug("poll_state upsert cui=%s env=%s last_polled_at=%s", cui, env, last_polled_at.isoformat())
# insert_synced_message — use the existing `cur`/result; log the row's identity:
_log.debug("synced_message insert msg_id=%s cui=%s env=%s", msg.msg_id, msg.cui, msg.env)
# finalize_zip_write:
_log.debug("zip finalized msg_id=%s cui=%s env=%s zip=%s", msg_id, cui, env, zip_path)
# update_pdf_path:
_log.debug("pdf path set msg_id=%s cui=%s env=%s pdf=%s", msg_id, cui, env, pdf_path)
# mark_email_sent:
_log.debug("email marked sent msg_id=%s cui=%s env=%s", msg_id, cui, env)
# mark_email_skipped:
_log.debug("email marked skipped msg_id=%s cui=%s env=%s reason=%s", msg_id, cui, env, reason)
# update_attempt:
_log.debug("attempt recorded msg_id=%s cui=%s env=%s error=%s", msg_id, cui, env, error)
```

> `init_schema` is `CREATE TABLE IF NOT EXISTS`-style and runs on every command, so use DEBUG-level for `schema initialized` to avoid INFO noise on every invocation (overrides the spec's "INFO when it migrates" note, which is awkward to detect with `IF NOT EXISTS`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/storage -v`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/efactura_sync/storage/db.py && uv run mypy src/efactura_sync/storage/db.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/efactura_sync/storage/db.py tests/storage
git commit -m "feat(storage): debug-log db mutations + schema init

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: Sync orchestrator step-level DEBUG logs

**Files:**
- Modify: `src/efactura_sync/sync.py`
- Test: `tests/test_sync.py` (append a caplog test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sync.py` (reuse the existing `FakeAnaf`, `RecordingMailer`, `db`, `archive_root`, `now_utc` fixtures/fakes already defined in that file):

```python
import logging


def test_process_one_message_logs_steps(db, archive_root, now_utc, caplog) -> None:
    # Build SyncDeps + a ListMessage exactly as the existing happy-path test does,
    # then assert step-level DEBUG lines are emitted.
    ...  # reuse the existing test's setup to call process_one_message(...)
    with caplog.at_level(logging.DEBUG, logger="efactura_sync.sync"):
        process_one_message(deps, my_cui="123", my_display_name=None, env="test",
                            access_token="tok", list_msg=list_msg, now=now_utc)
    assert any("email decision" in r.message for r in caplog.records)
```

> Implementer: copy the dependency/ListMessage construction from the nearest existing `process_one_message` happy-path test in `tests/test_sync.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sync.py -k logs_steps -v`
Expected: FAIL — no `email decision` record.

- [ ] **Step 3: Implement logs**

In `src/efactura_sync/sync.py` (logger `_log` already exists). Add DEBUG step lines in `process_one_message`:

```python
# after the download succeeds (step 2), inside the `if row.zip_path is None:` success branch:
_log.debug("downloaded msg_id=%s (%d bytes)", list_msg.msg_id, len(zip_bytes))

# after the zip is written (step 4), after finalize_zip_write:
_log.debug("zip written msg_id=%s tip=%s", list_msg.msg_id, list_msg.tip)

# after a PDF is written (step 5), after update_pdf_path:
_log.debug("pdf written msg_id=%s", list_msg.msg_id)

# right after computing skip_reason (step 6):
_log.debug("email decision msg_id=%s skip=%s", list_msg.msg_id, skip_reason)
```

And in `_zile_for_run`, before each return, log the resolved window once at the call site instead — simplest is to log in `run_for_cui` right after computing `zile` (next to the existing poll INFO line):

```python
zile = _zile_for_run(deps, cui=my_cui, env=env, now=now, override=zile_override)
_log.debug("zile resolved cui=%s zile=%d override=%s", my_cui, zile, zile_override)
new_msgs = deps.anaf.list_messages(cif=my_cui, zile=zile, access_token=access_token)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/efactura_sync/sync.py && uv run mypy src/efactura_sync/sync.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/efactura_sync/sync.py tests/test_sync.py
git commit -m "feat(sync): debug-log per-message steps + resolved zile

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: Full verification + README note

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the full suite + lint + types**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: all tests pass; ruff clean; mypy clean.

- [ ] **Step 2: Manually confirm logs appear end to end**

Run a real, side-effect-free command with DEBUG to eyeball the output (uses a temp config with `[logging] level = "DEBUG"`, or set the level in the real config). Example:

Run: `uv run efactura-sync cui list`
Expected (stderr): at least a `... DEBUG efactura_sync.cli: db opened path=...` line, with stdout still showing the normal `cui list` output.

- [ ] **Step 3: Add a README note**

In `README.md`, under the configuration section that documents `config.toml`, add a short note:

```markdown
### Logging

Set the log level in `config.toml`:

```toml
[logging]
level = "INFO"  # or DEBUG for verbose per-action logs
```

Logs are written to **stderr** (stdout carries normal command output). `INFO`
shows run milestones and external calls (ANAF, SMTP); `DEBUG` adds per-message
steps, HTTP details, and DB/file operations. Tokens, secrets, and passwords are
never logged.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): document config.toml [logging] level + stderr logs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-review notes (from plan author)

- **Spec coverage:** setup module (T1), `read_log_level` (T2), callback wire-up + command logs (T3), client (T4), oauth (T5), render (T6), mail (T7), files (T8), db (T9), sync (T10), README + final verify (T11). All spec "Components" map to a task.
- **Deviation logged:** the spec's "`init_schema` INFO when it migrates" is implemented as DEBUG (T9 step 3 note) because `CREATE TABLE IF NOT EXISTS` runs on every command and migration detection is not cheap — DEBUG avoids INFO noise on every invocation. Confirm this is acceptable; flip to INFO if preferred.
- **Security:** T4/T5/T7 tests assert that tokens/secrets/passwords do **not** appear in captured logs.
- **Test-fixture reuse:** several tasks instruct the implementer to reuse existing fakes/fixtures (`tests/conftest.py`, `tests/test_sync.py`, `tests/test_mail.py`) rather than inventing new ones; the env-var/temp-dir setup in `tests/test_cli.py` (T3) must be copied from the existing CLI tests since the exact `resolve_paths` env var is defined there.
