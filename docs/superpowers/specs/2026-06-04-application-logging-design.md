# Application logging: config-driven level + INFO/DEBUG at every action

**Date:** 2026-06-04
**Status:** Approved (pending spec review)

## Problem

The app has the *beginnings* of logging but it is inert:

- `sync.py` and `anaf/messages.py` call `logging.getLogger(__name__)` and emit a
  handful of `info`/`debug`/`warning` lines.
- `Config.log_level` is parsed from `[logging].level` in `config.toml` (default
  `INFO`) — but **nothing ever uses it**.
- There is **no logging configuration anywhere**: no `basicConfig`, no handler,
  no level set. Python's root logger therefore only emits `WARNING`+ to stderr
  with a bare format, so the existing `info`/`debug` calls are silently dropped.

Goal: when the app runs, every meaningful action emits an `INFO` (milestone /
external side-effect) or `DEBUG` (detail) log line, with the verbosity
controlled by the existing `[logging].level` setting in `config.toml`.

Non-goal for this change: log files/rotation, JSON/logfmt output, `-v/-vv`
flags, env-var overrides, or third-party (httpx/smtplib) wire logging. See
"Out of scope".

## Approach

Two parts:

1. **Wire up a central logging configuration** driven by `config.toml`. A new
   `setup_logging(level)` configures the package logger; a cheap
   `read_log_level(config_path)` reads only `[logging].level` so the level can
   be applied in the Typer `@app.callback` — for **every** command — without
   making `config.toml` mandatory for the DB-only commands.

2. **Add `INFO`/`DEBUG` log statements at every action site** across the
   modules, following a consistent level policy (below).

### Why configure in the callback (approach A)

The CLI has two command groups:

- **Config-loading commands** — `auth login`, `auth refresh`, `sync run` — call
  `_prepare()` → `load_config()`, which reads `config.toml` **and**
  `secrets.toml` and validates SMTP + ANAF credentials.
- **DB-only commands** — `cui add/list/remove`, `watch add/list/remove`,
  `status`, `replay` — never load config today; they only need `resolve_paths()`
  and the SQLite DB. They must keep working without a `config.toml`.

To make the configured level apply to **all** commands (one predictable rule:
the config level always wins), the `@app.callback` reads only the logging level
via a dedicated helper that does **not** require `secrets.toml` and does **not**
validate credentials. Full `load_config()` still runs later for the commands
that need it. Cost: one extra sub-millisecond TOML read for sync/auth (they read
`config.toml` twice — once for the level, once in full); no network, negligible.

## Components

### `src/efactura_sync/logging_setup.py` (new)

```python
"""Central logging configuration for the efactura-sync CLI."""

import logging
import sys

_PACKAGE_LOGGER = "efactura_sync"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: str) -> None:
    """Configure the ``efactura_sync`` package logger. Idempotent.

    Attaches a single ``StreamHandler(sys.stderr)`` with a human-readable
    formatter, sets the level, and disables propagation so third-party loggers
    (httpx, smtplib) are not configured by us. Safe to call more than once: a
    second call updates the level without adding a duplicate handler. Unknown
    level strings fall back to INFO with a warning.
    """
    logger = logging.getLogger(_PACKAGE_LOGGER)
    numeric = logging.getLevelName(level.upper())
    if not isinstance(numeric, int):
        numeric = logging.INFO
        bad = level
    else:
        bad = None

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

- Scoped to the `efactura_sync` logger (not the root logger) so we never emit
  httpx/smtplib internals.
- `propagate = False` prevents double-logging through the root logger.
- The module-level `_configured` guard makes repeated calls (callback +, in
  approach B, `_prepare`; here only the callback) safe — no duplicate handlers.
- Output goes to **stderr**, leaving stdout for `typer.echo` CLI output.

Example line:
`2026-06-04 21:00:01 INFO efactura_sync.sync: poll zile=7 new=3`

### `src/efactura_sync/config.py`

Add a helper next to `load_config` that reads only the logging level, cheaply
and defensively (no secrets, no validation):

```python
def read_log_level(config_path: Path) -> str:
    """Read ``[logging].level`` from config.toml; return "INFO" on any problem.

    Used by the CLI to configure logging before full config validation, so the
    level applies to commands that do not otherwise load config.
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

`load_config` is otherwise unchanged (it still parses `log_level` into
`Config`).

### `src/efactura_sync/cli.py`

In `@app.callback`, after `ctx.obj = resolve_paths()` succeeds, configure
logging from the config level:

```python
from efactura_sync.config import load_config, read_log_level
from efactura_sync.logging_setup import setup_logging

@app.callback()
def _main(ctx: typer.Context) -> None:
    try:
        ctx.obj = resolve_paths()
    except PathConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    setup_logging(read_log_level(ctx.obj.config_path))
```

(Resolve paths first — `read_log_level` needs `config_path`. If
`resolve_paths()` fails we exit before logging is set up, which is fine: that
error is already reported via `typer.echo`.)

Then add a module logger `_log = logging.getLogger(__name__)` and log:

- **DEBUG** at the start of each command with its key args, e.g.
  `_log.debug("cui add cui=%s name=%s", cui, name)`,
  `_log.debug("sync run cui=%s env=%s dry_run=%s zile=%s", cui, env, dry_run, zile)`.
- **INFO** for the token refresh that happens inside `sync run`
  (`_log.info("token refresh cui=%s", m.cui)` around the existing
  `refresh_access_token` call) — this is an external side-effect.
- **DEBUG** for `_open_db` (`db opened path=%s`) and dry-run branch decisions.

The existing `typer.echo` user-facing output (the `OK — ...` / `cui=...
processed=...` lines) stays exactly as-is; logging is additive and goes to
stderr.

### `src/efactura_sync/anaf/client.py`

Add `_log = logging.getLogger(__name__)`. In `list_messages` and `download`:

- **DEBUG** before the GET: method, full URL, params (cif/zile or id) — never
  the token.
- **DEBUG** after `_classify`: response status and `len(resp.content)` bytes.
- **INFO** summarizing the outcome: `list_messages cif=%s zile=%d -> %d messages`
  and `download msg_id=%s -> %d bytes`.

### `src/efactura_sync/anaf/oauth.py`

Add `_log`. Log **metadata only — never token/secret values**:

- **DEBUG** in `load_token` / `save_token`: `token loaded/saved cui=%s env=%s`.
- **INFO** in `exchange_code`: `oauth exchange ok cui=%s env=%s expires_at=%s`.
- **INFO** in `refresh_access_token`: `oauth refresh ok cui=%s expires_at=%s`;
  **DEBUG** before the POST (`oauth refresh start cui=%s`). The error branches
  already raise typed exceptions; a `_log.warning` on the `invalid_grant` /
  non-200 paths (status only, no body secrets) is acceptable but optional.

### `src/efactura_sync/render.py`

Add `_log`. In `render`:

- **DEBUG** before the POST: `xmltopdf standard=%s xml_bytes=%d`.
- **INFO** on success: `rendered PDF %d bytes`.

### `src/efactura_sync/mail.py`

Add `_log` to the `Mailer` class methods (rendering functions stay pure / silent):

- **DEBUG** in `_connect` / start of `send`: `smtp connect host=%s port=%d tls=%s`.
- **INFO** on success: `email sent subject=%r to=%s` (no body, no password).

### `src/efactura_sync/storage/files.py`

Add `_log`. In `atomic_write`: **DEBUG** `atomic_write %s (%d bytes)`. In
`sweep_partials`: **DEBUG** `swept %d stale .partial file(s)` when `removed`.

### `src/efactura_sync/storage/db.py`

Add `_log`. Keep it light to avoid noise:

- **INFO** once in `init_schema`: `schema initialized` (only when it actually
  creates/migrates; if cheap to detect, otherwise DEBUG).
- **DEBUG** in the mutating helpers (`insert_synced_message`,
  `finalize_zip_write`, `update_pdf_path`, `update_attempt`, `mark_email_sent`,
  `mark_email_skipped`, `upsert_poll_state`, `add_monitored_cui`,
  `remove_monitored_cui`, `add_watched_counterparty`,
  `remove_watched_counterparty`) with the key identifiers (msg_id/cui/env).

### `src/efactura_sync/sync.py`

Keep the existing `INFO` lines (run start / resume / poll / done). Add:

- **DEBUG** per-message step transitions in `process_one_message`: row
  insert, download (`download msg_id=%s`), parse, zip write, pdf render, email
  decision (`email decision msg_id=%s skip=%s`), email sent / skipped.
- **DEBUG** in `_zile_for_run`: `zile resolved cui=%s zile=%d (override=%s)`.

## Level policy (summary)

| Level | Used for |
|-------|----------|
| **INFO** | Milestones & external side-effects: run start/poll/done; ANAF `list_messages`/`download`; PDF render; email sent; OAuth exchange/refresh; `cui`/`watch` add/remove; schema init |
| **DEBUG** | Details: HTTP method+URL+status+byte counts; per-message step transitions; email skip-reason decisions; file writes; token load/save; DB mutations; command-invocation args; resolved `zile` |

**Security:** never log access/refresh tokens, client secrets, or SMTP
passwords. Log only metadata — expiry timestamps, cui, env, host, byte counts,
status codes, msg ids.

## Error handling

- `read_log_level` never raises: missing file, bad TOML, or missing
  `[logging]`/`level` all return `"INFO"`.
- `setup_logging` with an unknown level string falls back to `INFO` and logs a
  warning rather than raising.
- Logging is purely additive; no control flow, exit codes, or `typer.echo`
  output change. The existing `sync run` failure-email path is untouched.

## Testing

`tests/test_logging_setup.py` (new):

- `setup_logging("DEBUG")` sets the `efactura_sync` logger level to DEBUG and
  attaches exactly one handler; a second call (e.g. `setup_logging("INFO")`)
  updates the level and does **not** add a second handler.
- `setup_logging("garbage")` falls back to `INFO` (logger level == INFO).
- `read_log_level` returns the configured value for a temp `config.toml` with
  `[logging] level = "DEBUG"`; returns `"INFO"` for a missing file, malformed
  TOML, and a file with no `[logging]` section.
- An emitted record is visible via `caplog` / a captured stderr handler at the
  configured level (sanity check that records flow).

Existing suites:

- `tests/test_cli.py` / others assert on `typer.echo` **stdout**; logging goes
  to **stderr**, so those assertions are unaffected. Run the full suite to
  confirm no test captures root-logger output that now changes.
- Where a test constructs the CLI runner, the callback will call
  `setup_logging`; ensure no test asserts "no handlers" on the root logger.

## Out of scope

- Log files / rotation / a `logs/` directory (CLI/stderr only for now).
- JSON or logfmt output formats (human-readable only).
- `-v/-vv` flags or an env-var override (level comes from `config.toml`).
- httpx / smtplib wire-level logging (package logger is scoped to
  `efactura_sync`; enabling third-party loggers is a future opt-in).
- Per-CUI log files or a log tail richer than today's failure email.
