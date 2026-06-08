# Environment-Driven Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve every filesystem path (sqlite db, config, secrets, tokens, archive) exclusively from environment variables, deleting the CLI flags, XDG resolution, `platformdirs` fallback, and the `[archive].root` config option.

**Architecture:** A new `paths.py` module owns all path resolution via `resolve_paths(environ)`, returning a frozen `Paths` dataclass and raising `PathConfigError` for missing required base vars. The Typer top-level callback calls it once, stores `Paths` on `ctx.obj`, and exits 2 on a missing var. `config.py` loses its archive-root logic; the archive dir now comes from `Paths.archive_dir`.

**Tech Stack:** Python 3.14, Typer, pytest, uv, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-06-02-env-driven-paths-design.md`

**Environment variables:**

| Variable | Required | Resolves |
|---|---|---|
| `EFACTURA_SYNC_CONFIG_DIR` | yes | base for `config.toml`, `secrets.toml`, and defaults below |
| `EFACTURA_SYNC_ARCHIVE_DIR` | yes | archive root |
| `EFACTURA_SYNC_DB` | optional override | `state.db` (default `<config-dir>/state.db`) |
| `EFACTURA_SYNC_TOKENS_DIR` | optional override | tokens dir (default `<config-dir>/tokens`) |

**File map:**
- Create: `src/efactura_sync/paths.py` — env var → `Paths` resolution.
- Create: `tests/test_paths.py` — unit tests for `resolve_paths`.
- Modify: `src/efactura_sync/cli.py` — drop XDG helpers/options/flags; call `resolve_paths`; use `Paths`.
- Modify: `tests/test_cli.py` — drive the CLI via env vars instead of flags.
- Modify: `src/efactura_sync/config.py` — remove `archive_root`, `[archive]` parsing, `platformdirs`.
- Modify: `tests/test_config.py` — remove archive-root tests.
- Modify: `pyproject.toml` — drop `platformdirs` dependency.
- Modify: `README.md` — document the env-var scheme.

---

## Task 1 — `paths.py` env-var resolution (TDD)

**Files:**
- Create: `src/efactura_sync/paths.py`
- Create: `tests/test_paths.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paths.py`:

```python
"""Tests for environment-driven path resolution."""

from pathlib import Path

import pytest

from efactura_sync.paths import PathConfigError, Paths, resolve_paths


def _base_env(tmp_path: Path) -> dict[str, str]:
    return {
        "EFACTURA_SYNC_CONFIG_DIR": str(tmp_path / "cfg"),
        "EFACTURA_SYNC_ARCHIVE_DIR": str(tmp_path / "arch"),
    }


def test_resolve_derives_all_paths_from_bases(tmp_path: Path) -> None:
    paths = resolve_paths(_base_env(tmp_path))
    cfg = tmp_path / "cfg"
    assert paths == Paths(
        config_path=cfg / "config.toml",
        secrets_path=cfg / "secrets.toml",
        tokens_dir=cfg / "tokens",
        db_path=cfg / "state.db",
        archive_dir=tmp_path / "arch",
    )


def test_db_override_relocates_only_db(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["EFACTURA_SYNC_DB"] = str(tmp_path / "custom" / "x.db")
    paths = resolve_paths(env)
    assert paths.db_path == tmp_path / "custom" / "x.db"
    assert paths.tokens_dir == tmp_path / "cfg" / "tokens"


def test_tokens_override_relocates_only_tokens(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["EFACTURA_SYNC_TOKENS_DIR"] = str(tmp_path / "toks")
    paths = resolve_paths(env)
    assert paths.tokens_dir == tmp_path / "toks"
    assert paths.db_path == tmp_path / "cfg" / "state.db"


def test_missing_config_dir_raises(tmp_path: Path) -> None:
    env = {"EFACTURA_SYNC_ARCHIVE_DIR": str(tmp_path / "arch")}
    with pytest.raises(PathConfigError, match="EFACTURA_SYNC_CONFIG_DIR is not set"):
        resolve_paths(env)


def test_missing_archive_dir_raises(tmp_path: Path) -> None:
    env = {"EFACTURA_SYNC_CONFIG_DIR": str(tmp_path / "cfg")}
    with pytest.raises(PathConfigError, match="EFACTURA_SYNC_ARCHIVE_DIR is not set"):
        resolve_paths(env)


def test_empty_base_treated_as_missing(tmp_path: Path) -> None:
    env = {"EFACTURA_SYNC_CONFIG_DIR": "", "EFACTURA_SYNC_ARCHIVE_DIR": str(tmp_path)}
    with pytest.raises(PathConfigError, match="EFACTURA_SYNC_CONFIG_DIR is not set"):
        resolve_paths(env)


def test_empty_override_falls_back_to_default(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["EFACTURA_SYNC_DB"] = ""
    env["EFACTURA_SYNC_TOKENS_DIR"] = ""
    paths = resolve_paths(env)
    assert paths.db_path == tmp_path / "cfg" / "state.db"
    assert paths.tokens_dir == tmp_path / "cfg" / "tokens"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_paths.py -q`
Expected: collection/import error — `ModuleNotFoundError: No module named 'efactura_sync.paths'`.

- [ ] **Step 3: Write `src/efactura_sync/paths.py`**

```python
"""Resolve all filesystem paths from environment variables.

Every path the CLI uses — the SQLite database, config/secrets files, OAuth
tokens directory, and archive root — comes from environment variables and
nothing else. There is no XDG, platformdirs, $HOME, or cwd fallback.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from efactura_sync.errors import ConfigError

_CONFIG_DIR_VAR = "EFACTURA_SYNC_CONFIG_DIR"
_ARCHIVE_DIR_VAR = "EFACTURA_SYNC_ARCHIVE_DIR"
_DB_VAR = "EFACTURA_SYNC_DB"
_TOKENS_DIR_VAR = "EFACTURA_SYNC_TOKENS_DIR"


class PathConfigError(ConfigError):
    """A required path environment variable is unset or empty."""


@dataclass(frozen=True)
class Paths:
    config_path: Path
    secrets_path: Path
    tokens_dir: Path
    db_path: Path
    archive_dir: Path


def _required(environ: Mapping[str, str], var: str) -> Path:
    value = environ.get(var, "")
    if not value:
        raise PathConfigError(f"{var} is not set")
    return Path(value)


def resolve_paths(environ: Mapping[str, str] = os.environ) -> Paths:
    """Build a `Paths` from the process environment.

    Both base vars are required (config checked first). Override vars are used
    only when set and non-empty; otherwise the path derives from the config base.
    """
    config_dir = _required(environ, _CONFIG_DIR_VAR)
    archive_dir = _required(environ, _ARCHIVE_DIR_VAR)

    db_override = environ.get(_DB_VAR, "")
    tokens_override = environ.get(_TOKENS_DIR_VAR, "")

    return Paths(
        config_path=config_dir / "config.toml",
        secrets_path=config_dir / "secrets.toml",
        tokens_dir=Path(tokens_override) if tokens_override else config_dir / "tokens",
        db_path=Path(db_override) if db_override else config_dir / "state.db",
        archive_dir=archive_dir,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_paths.py -q`
Expected: 7 passed.

- [ ] **Step 5: Lint + type-check the new module**

Run: `uv run ruff check src/efactura_sync/paths.py tests/test_paths.py && uv run mypy src/efactura_sync/paths.py`
Expected: all checks pass; no type issues.

- [ ] **Step 6: Commit**

```bash
git add src/efactura_sync/paths.py tests/test_paths.py
git commit -m "feat(paths): resolve all paths from environment variables"
```

---

## Task 2 — Wire `paths` into the CLI; drop flags/XDG

**Files:**
- Modify: `src/efactura_sync/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add the import**

In `src/efactura_sync/cli.py`, add this import alongside the other `efactura_sync.*` imports (e.g. directly after the `from efactura_sync.mail import ...` line):

```python
from efactura_sync.paths import PathConfigError, Paths, resolve_paths
```

- [ ] **Step 2: Delete the XDG helpers and path options**

In `src/efactura_sync/cli.py`, delete the entire block that begins with `def _xdg_base_dir(` and ends with the closing `)` of `_OPT_DB = typer.Option(...)`. That removes: `_xdg_base_dir`, `_xdg_config_home`, `_xdg_data_home`, `_DEFAULT_CONFIG_DIR`, `_DEFAULT_DATA_DIR`, `_OPT_CONFIG`, `_OPT_SECRETS`, `_OPT_TOKENS_DIR`, `_OPT_DB`. The exact block to remove:

```python
def _xdg_base_dir(env_var: str, fallback_subpath: tuple[str, ...]) -> Path:
    """Resolve an XDG base directory per the spec.

    Returns ``$<env_var>`` if it is set, non-empty, and absolute. Otherwise
    returns ``Path.home().joinpath(*fallback_subpath)``. Relative values are
    ignored per the XDG Base Directory Specification.
    """
    raw = os.environ.get(env_var, "")
    if raw:
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate
    return Path.home().joinpath(*fallback_subpath)


def _xdg_config_home() -> Path:
    return _xdg_base_dir("XDG_CONFIG_HOME", (".config",))


def _xdg_data_home() -> Path:
    return _xdg_base_dir("XDG_DATA_HOME", (".local", "share"))


_DEFAULT_CONFIG_DIR = _xdg_config_home() / "efactura-sync"
_DEFAULT_DATA_DIR = _xdg_data_home() / "efactura-sync"
_OPT_CONFIG = typer.Option(
    _DEFAULT_CONFIG_DIR / "config.toml",
    "--config",
    help="Path to config.toml",
)
_OPT_SECRETS = typer.Option(
    _DEFAULT_CONFIG_DIR / "secrets.toml",
    "--secrets",
    help="Path to secrets.toml",
)
_OPT_TOKENS_DIR = typer.Option(
    _DEFAULT_CONFIG_DIR / "tokens",
    "--tokens-dir",
    help="Directory holding per-CUI OAuth token files.",
)
_OPT_DB = typer.Option(
    _DEFAULT_DATA_DIR / "state.db",
    "--db",
    help="Path to SQLite state database.",
)
```

Leave the lines that follow (`_OPT_CUI_LOGIN`, `_OPT_CUI`, `_OPT_ENV`, etc.) untouched.

- [ ] **Step 3: Replace `_CliContext` + the callback**

In `src/efactura_sync/cli.py`, replace this block:

```python
@dataclass(frozen=True)
class _CliContext:
    config_path: Path
    secrets_path: Path
    tokens_dir: Path
    db_path: Path


@app.callback()
def _main(
    ctx: typer.Context,
    config: Path = _OPT_CONFIG,
    secrets: Path = _OPT_SECRETS,
    tokens_dir: Path = _OPT_TOKENS_DIR,
    db_path: Path = _OPT_DB,
) -> None:
    ctx.obj = _CliContext(
        config_path=config,
        secrets_path=secrets,
        tokens_dir=tokens_dir,
        db_path=db_path,
    )
```

with:

```python
@app.callback()
def _main(ctx: typer.Context) -> None:
    try:
        ctx.obj = resolve_paths()
    except PathConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
```

- [ ] **Step 4: Update `_ctx` to return `Paths`**

In `src/efactura_sync/cli.py`, replace:

```python
def _ctx(ctx: typer.Context) -> _CliContext:
    obj = ctx.obj
    if not isinstance(obj, _CliContext):
        raise RuntimeError(f"typer context not initialized: got {type(obj).__name__}")
    return obj
```

with:

```python
def _ctx(ctx: typer.Context) -> Paths:
    obj = ctx.obj
    if not isinstance(obj, Paths):
        raise RuntimeError(f"typer context not initialized: got {type(obj).__name__}")
    return obj
```

- [ ] **Step 5: Source the archive dir from `Paths` in `sync run`**

In `src/efactura_sync/cli.py`, inside `sync_run_cmd`, replace:

```python
        archive_root = cfg.archive_root
```

with:

```python
        archive_root = cli_ctx.archive_dir
```

- [ ] **Step 6: Remove now-unused imports**

In `src/efactura_sync/cli.py`, delete `import os` and `from dataclasses import dataclass` (both are now unused — `os` was only used by `_xdg_base_dir`, `dataclass` only by `_CliContext`).

- [ ] **Step 7: Confirm the source compiles and the lint flags only test breakage**

Run: `uv run ruff check src/efactura_sync/cli.py && uv run mypy src/efactura_sync/cli.py`
Expected: ruff clean, mypy clean. (If ruff reports an unused import, remove it; if it reports `os`/`dataclass` still used, restore that single import.)

- [ ] **Step 8: Add the autouse env fixture in `tests/test_cli.py`**

In `tests/test_cli.py`, immediately after the `runner = CliRunner()` line, add:

```python
@pytest.fixture(autouse=True)
def _env_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the CLI at tmp_path via env vars and write valid config + secrets.

    Autouse so every CLI test runs against a working config dir. Tests that need
    a missing-config or unset-env scenario override these via monkeypatch.
    """
    _write_fixture_files(tmp_path)
    monkeypatch.setenv("EFACTURA_SYNC_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("EFACTURA_SYNC_ARCHIVE_DIR", str(tmp_path / "archive"))
```

- [ ] **Step 9: Delete the `_cli_args` helper**

In `tests/test_cli.py`, delete the whole `_cli_args` function:

```python
def _cli_args(tmp_path: Path, db_path: Path) -> list[str]:
    """Build the global option args for any CLI invocation."""
    config_file, secrets_file = _write_fixture_files(tmp_path)
    return [
        "--config",
        str(config_file),
        "--secrets",
        str(secrets_file),
        "--tokens-dir",
        str(tmp_path / "tokens"),
        "--db",
        str(db_path),
    ]
```

- [ ] **Step 10: Rewrite every `_cli_args(...)` call to plain args**

Run this in-place edit to strip the helper prefix (handles same-line and multiline `+`):

```bash
perl -0pi -e 's/_cli_args\(tmp_path, db_path\)\s*\+\s*/ /g' tests/test_cli.py
```

After this, calls read `runner.invoke(app, ["cui", "add", ...])`. The `db_path = tmp_path / "state.db"` locals stay valid — that is still the default DB location (`<config-dir>/state.db` with `EFACTURA_SYNC_CONFIG_DIR=tmp_path`), so the direct-DB assertions keep working.

- [ ] **Step 11: Fix the inline-flag auth tests**

Five tests still pass `--config/--secrets/--tokens-dir` inline. Edit each so it relies on the autouse fixture.

In `test_auth_login_invokes_oauth_and_writes_token`, replace:

```python
    config_file, secrets_file = _write_fixture_files(tmp_path)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_file),
            "--secrets",
            str(secrets_file),
            "--tokens-dir",
            str(tmp_path / "tokens"),
            "auth",
            "login",
            "--cui",
            "12345678",
            "--env",
            "prod",
        ],
    )
```

with:

```python
    result = runner.invoke(
        app,
        ["auth", "login", "--cui", "12345678", "--env", "prod"],
    )
```

In `test_invalid_env_exits_two`, replace its whole body:

```python
def test_invalid_env_exits_two(tmp_path: Path) -> None:
    config_file, secrets_file = _write_fixture_files(tmp_path)

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_file),
            "--secrets",
            str(secrets_file),
            "--tokens-dir",
            str(tmp_path / "tokens"),
            "auth",
            "login",
            "--cui",
            "12345678",
            "--env",
            "staging",
        ],
    )
    assert result.exit_code == 2
    # Error message goes to stderr; combine streams for robustness across Click versions.
    combined = (result.stdout or "") + (result.stderr or "")
    assert "staging" in combined or "unknown env" in combined
```

with:

```python
def test_invalid_env_exits_two(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["auth", "login", "--cui", "12345678", "--env", "staging"],
    )
    assert result.exit_code == 2
    # Error message goes to stderr; combine streams for robustness across Click versions.
    combined = (result.stdout or "") + (result.stderr or "")
    assert "staging" in combined or "unknown env" in combined
```

In `test_auth_refresh_loads_and_writes_new_token`, replace:

```python
    config_file, secrets_file = _write_fixture_files(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config_file),
            "--secrets",
            str(secrets_file),
            "--tokens-dir",
            str(tokens_dir),
            "auth",
            "refresh",
            "--cui",
            "12345678",
            "--env",
            "prod",
        ],
    )
```

with:

```python
    result = runner.invoke(
        app,
        ["auth", "refresh", "--cui", "12345678", "--env", "prod"],
    )
```

(The `tokens_dir = tmp_path / "tokens"` local at the top of that test stays — it is the default tokens dir and is used by the pre-write and the `load_token` assertion.)

In `test_auth_login_missing_config_exits_nonzero`, replace its whole body:

```python
def test_auth_login_missing_config_exits_nonzero(tmp_path: Path) -> None:
    # Don't write the config file.
    result = runner.invoke(
        app,
        [
            "--config",
            str(tmp_path / "missing-config.toml"),
            "--secrets",
            str(tmp_path / "missing-secrets.toml"),
            "--tokens-dir",
            str(tmp_path / "tokens"),
            "auth",
            "login",
            "--cui",
            "12345678",
            "--env",
            "prod",
        ],
    )
    assert result.exit_code != 0
```

with:

```python
def test_auth_login_missing_config_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point at an empty config dir so config.toml/secrets.toml are absent.
    monkeypatch.setenv("EFACTURA_SYNC_CONFIG_DIR", str(tmp_path / "empty"))
    result = runner.invoke(
        app,
        ["auth", "login", "--cui", "12345678", "--env", "prod"],
    )
    assert result.exit_code != 0
```

In `test_auth_login_propagates_oauth_failure`, replace:

```python
    config_file, secrets_file = _write_fixture_files(tmp_path)
    result = runner.invoke(
        app,
        [
            "--config",
            str(config_file),
            "--secrets",
            str(secrets_file),
            "--tokens-dir",
            str(tmp_path / "tokens"),
            "auth",
            "login",
            "--cui",
            "12345678",
            "--env",
            "prod",
        ],
    )
```

with:

```python
    result = runner.invoke(
        app,
        ["auth", "login", "--cui", "12345678", "--env", "prod"],
    )
```

- [ ] **Step 12: Replace the `TestXdgBaseDir` class with a missing-env test**

In `tests/test_cli.py`, delete the entire `class TestXdgBaseDir:` block (through the end of its last method) and the `_xdg_base_dir` import it relies on (search for `_xdg_base_dir` and remove the `from efactura_sync.cli import ... _xdg_base_dir` import). In its place add:

```python
def test_missing_config_dir_env_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EFACTURA_SYNC_CONFIG_DIR", raising=False)
    result = runner.invoke(app, ["cui", "list"])
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "EFACTURA_SYNC_CONFIG_DIR is not set" in combined
```

- [ ] **Step 13: Run the full CLI test file**

Run: `uv run pytest tests/test_cli.py -q`
Expected: all pass. If a test that opens the DB directly fails, confirm its `db_path = tmp_path / "state.db"` line is intact (default DB location).

- [ ] **Step 14: Lint + type-check**

Run: `uv run ruff check src/efactura_sync/cli.py tests/test_cli.py && uv run mypy src/efactura_sync`
Expected: clean. Remove any import ruff reports as unused (e.g. a now-unused `Path` is unlikely — `Path` is still used in test signatures and `db_path` locals).

- [ ] **Step 15: Commit**

```bash
git add src/efactura_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): resolve paths from env vars; drop --config/--secrets/--tokens-dir/--db and XDG"
```

---

## Task 3 — Remove `archive_root` from config; drop `platformdirs`

**Files:**
- Modify: `src/efactura_sync/config.py`
- Modify: `tests/test_config.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Update the config tests first (they will fail)**

In `tests/test_config.py`:

1. Delete the assertion `assert cfg.archive_root.is_absolute()` from the main happy-path test.
2. Delete the entire `def test_load_config_archive_root_override(tmp_path: Path) -> None:` function.
3. Delete the entire `def test_load_config_archive_root_expandvars(...)` function.

- [ ] **Step 2: Run the config tests to verify the archive tests are gone and the rest still reference `archive_root`**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS (the remaining tests do not touch `archive_root`). If any failure mentions `archive_root`, a reference was missed in Step 1 — remove it.

- [ ] **Step 3: Remove `archive_root` from `Config`**

In `src/efactura_sync/config.py`, delete the `archive_root: Path` field from the `Config` dataclass:

```python
@dataclass(frozen=True)
class Config:
    archive_root: Path
    smtp: SmtpConfig
    anaf: AnafConfig
    log_level: str
```

becomes:

```python
@dataclass(frozen=True)
class Config:
    smtp: SmtpConfig
    anaf: AnafConfig
    log_level: str
```

- [ ] **Step 4: Remove the archive-root parsing block**

In `src/efactura_sync/config.py`, delete this block from `load_config`:

```python
    archive_root_raw = cfg.get("archive", {}).get("root")
    if archive_root_raw:
        expanded = os.path.expandvars(str(archive_root_raw))
        archive_root = Path(expanded).expanduser()
    else:
        archive_root = user_data_path("efactura-sync", appauthor=False) / "archive"

```

- [ ] **Step 5: Remove `archive_root` from the `Config(...)` construction**

In `src/efactura_sync/config.py`, delete the `archive_root=archive_root,` line from the final `return Config(...)`:

```python
    return Config(
        archive_root=archive_root,
        smtp=smtp,
        anaf=anaf,
        log_level=str(log_cfg.get("level", "INFO")),
    )
```

becomes:

```python
    return Config(
        smtp=smtp,
        anaf=anaf,
        log_level=str(log_cfg.get("level", "INFO")),
    )
```

- [ ] **Step 6: Remove the now-unused imports**

In `src/efactura_sync/config.py`, delete `import os` and `from platformdirs import user_data_path` (both were used only by the archive block). Keep `from pathlib import Path` (still used by `_read_toml`).

- [ ] **Step 7: Run config tests + type-check**

Run: `uv run pytest tests/test_config.py -q && uv run ruff check src/efactura_sync/config.py && uv run mypy src/efactura_sync/config.py`
Expected: tests pass; ruff clean (no unused imports); mypy clean.

- [ ] **Step 8: Drop the `platformdirs` dependency**

In `pyproject.toml`, delete the dependency line:

```
    "platformdirs>=4.2",
```

- [ ] **Step 9: Refresh the lockfile**

Run: `uv lock`
Expected: `uv.lock` updates, removing `platformdirs` (and any deps it pulled in that nothing else needs).

- [ ] **Step 10: Full suite + checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: all green; `platformdirs` no longer imported anywhere (`grep -rn platformdirs src/` returns nothing).

- [ ] **Step 11: Commit**

```bash
git add src/efactura_sync/config.py tests/test_config.py pyproject.toml uv.lock
git commit -m "refactor(config): drop archive_root and platformdirs; archive dir now from env"
```

---

## Task 4 — README documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the "Configuration paths" section**

In `README.md`, replace the entire `## Configuration paths` section (from that heading through the `--db <path>` bullet, ending just before `## Onboard a CUI`) with:

````markdown
## Configuration paths

All paths come from environment variables — there is no XDG, `$HOME`, or
config-file fallback. The two base variables are **required**; every command
exits with code `2` if one is unset:

| Variable | Required | Resolves |
|---|---|---|
| `EFACTURA_SYNC_CONFIG_DIR` | yes | holds `config.toml`, `secrets.toml`, and the defaults below |
| `EFACTURA_SYNC_ARCHIVE_DIR` | yes | archive root for downloaded ZIPs and rendered PDFs |
| `EFACTURA_SYNC_DB` | optional | path to the SQLite database (default `$EFACTURA_SYNC_CONFIG_DIR/state.db`) |
| `EFACTURA_SYNC_TOKENS_DIR` | optional | per-CUI OAuth token directory (default `$EFACTURA_SYNC_CONFIG_DIR/tokens`) |

`config.toml` and `secrets.toml` always live directly under
`EFACTURA_SYNC_CONFIG_DIR`. Set the variables once in your shell profile or the
systemd/cron environment:

```bash
export EFACTURA_SYNC_CONFIG_DIR="$HOME/.config/efactura-sync"
export EFACTURA_SYNC_ARCHIVE_DIR="$HOME/efactura-archive"
uv run efactura-sync status --env prod
```

A missing required variable fails fast:

```
$ efactura-sync sync run
error: EFACTURA_SYNC_CONFIG_DIR is not set
```
````

- [ ] **Step 2: Fix the `scp` example in "Onboard a CUI"**

In `README.md`, the onboarding `scp` line hard-codes `~/.config/efactura-sync/tokens/`. Replace:

```bash
scp ~/.config/efactura-sync/tokens/12345678.prod.json server:~/.config/efactura-sync/tokens/
```

with:

```bash
scp "$EFACTURA_SYNC_CONFIG_DIR/tokens/12345678.prod.json" \
    server:"$EFACTURA_SYNC_CONFIG_DIR/tokens/"
```

- [ ] **Step 3: Add the env vars to the "Daily run (server)" example**

In `README.md`, replace:

````markdown
```bash
uv run efactura-sync sync run --env prod
```

Schedule via cron once daily (example, 03:00 local):

```cron
0 3 * * * /usr/bin/env -S /home/youruser/.local/bin/uv run --project /home/youruser/efactura-sync efactura-sync sync run --env prod
```
````

with:

````markdown
```bash
export EFACTURA_SYNC_CONFIG_DIR=/home/youruser/.config/efactura-sync
export EFACTURA_SYNC_ARCHIVE_DIR=/home/youruser/efactura-archive
uv run efactura-sync sync run --env prod
```

Schedule via cron once daily (example, 03:00 local). cron runs with a bare
environment, so set the required variables in the crontab:

```cron
EFACTURA_SYNC_CONFIG_DIR=/home/youruser/.config/efactura-sync
EFACTURA_SYNC_ARCHIVE_DIR=/home/youruser/efactura-archive
0 3 * * * /usr/bin/env -S /home/youruser/.local/bin/uv run --project /home/youruser/efactura-sync efactura-sync sync run --env prod
```
````

- [ ] **Step 4: Fix the "Storage layout" override note + example path**

In `README.md`, in the `## Storage layout` section, replace the opening of the code block:

```
~/.local/share/efactura-sync/
  state.db                          # SQLite — dedup ledger + resume log
  archive/<CUI>/<YYYY>/<MM>/
```

with:

```
$EFACTURA_SYNC_CONFIG_DIR/state.db    # SQLite — dedup ledger + resume log
$EFACTURA_SYNC_ARCHIVE_DIR/<CUI>/<YYYY>/<MM>/
```

and adjust the remaining lines in that code block so the archive paths are
rooted at `$EFACTURA_SYNC_ARCHIVE_DIR/<CUI>/...` instead of
`archive/<CUI>/...`. Then replace the trailing sentence:

```
Path partitioning uses the **invoice issue date** (Bucharest local) for invoices and the ANAF `data_creare` for messages. Override the archive root via `[archive].root` in `config.toml`.
```

with:

```
Path partitioning uses the **invoice issue date** (Bucharest local) for invoices and the ANAF `data_creare` for messages. The archive root is `$EFACTURA_SYNC_ARCHIVE_DIR`.
```

- [ ] **Step 5: Verify no stale references remain**

Run: `grep -nE "XDG|--config|--secrets|--tokens-dir|--db|\[archive\]|platformdirs|\.local/share|\.config/efactura-sync/tokens" README.md`
Expected: no matches except intentional ones (the `export ...=$HOME/.config/efactura-sync` examples are fine — they are user-chosen values, not hard-coded defaults). There should be **no** `--config/--secrets/--tokens-dir/--db`, no `XDG`, no `[archive]`, no `platformdirs`.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs(readme): document env-var path configuration"
```

---

## Task 5 — Final verification

- [ ] **Step 1: Full check**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: all tests pass; ruff clean; mypy clean.

- [ ] **Step 2: Smoke-test missing-var behavior**

Run:

```bash
env -u EFACTURA_SYNC_CONFIG_DIR -u EFACTURA_SYNC_ARCHIVE_DIR \
  uv run efactura-sync cui list; echo "exit=$?"
```

Expected: prints `error: EFACTURA_SYNC_CONFIG_DIR is not set` to stderr and `exit=2`.

- [ ] **Step 3: Smoke-test the happy path**

Run:

```bash
tmp=$(mktemp -d)
EFACTURA_SYNC_CONFIG_DIR="$tmp" EFACTURA_SYNC_ARCHIVE_DIR="$tmp/archive" \
  uv run efactura-sync cui list; echo "exit=$?"
```

Expected: prints the "no monitored CUIs yet" message and `exit=0` (it creates `$tmp/state.db`). Note: `cui list` does not read `config.toml`, so this works without writing config files; commands like `sync run`/`status`/`auth` still require valid `config.toml`+`secrets.toml` under `$tmp`.

- [ ] **Step 4: Confirm `platformdirs` is fully gone**

Run: `grep -rn platformdirs src/ pyproject.toml uv.lock | grep -v '^uv.lock:.*# ' || echo "clean"`
Expected: no `platformdirs` in `src/` or `pyproject.toml`. (It may linger in `uv.lock` only if another dependency still needs it; if `uv lock` removed it, even better.)

- [ ] **Step 5: Confirm git log is clean**

Run: `git log --oneline -6`
Expected: the four feature commits from Tasks 1–4 plus this plan, in order.

---

## Self-Review Notes

- **Spec coverage:** env-var table (Task 1 `paths.py` + Task 4 README); required/optional semantics (Task 1 tests); hard-error exit 2 (Task 2 callback + Task 2 Step 12 test + Task 5 smoke); remove flags/XDG (Task 2); remove `[archive].root`/`platformdirs` (Task 3); README (Task 4). All spec sections map to a task.
- **No fallback anywhere:** `resolve_paths` has no `$HOME`/XDG/cwd/platformdirs branch; `config.py` archive fallback removed in Task 3.
- **Type consistency:** `Paths` fields (`config_path`, `secrets_path`, `tokens_dir`, `db_path`, `archive_dir`) are used unchanged by `_ctx`, `_prepare`, `status_cmd`, and `sync_run_cmd` (which read `.config_path`, `.secrets_path`, `.tokens_dir`, `.db_path`, `.archive_dir`). `PathConfigError` subclasses `ConfigError` so existing error handling stays valid.
- **Ordering:** Task 2 stops referencing `cfg.archive_root` (uses `cli_ctx.archive_dir`) before Task 3 removes that field, so the suite stays green between tasks.
