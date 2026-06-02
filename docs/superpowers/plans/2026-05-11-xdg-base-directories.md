# XDG Base Directory Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the config directory and state-DB directory honor the XDG Base Directory Specification, so users who set `$XDG_CONFIG_HOME` / `$XDG_DATA_HOME` get the CLI defaults pointed at the right place without having to pass per-file overrides every invocation.

**Architecture:** Two pure helper functions in `cli.py` resolve the XDG base dirs once at module load. The existing `_DEFAULT_CONFIG_DIR` constant and the `_OPT_DB` default are rewritten to use those helpers. A new `_DEFAULT_DATA_DIR` constant is introduced for symmetry. The four existing CLI flags (`--config`, `--secrets`, `--tokens-dir`, `--db`) keep their current semantics — they continue to override the derived defaults.

**Tech Stack:** Python 3.14, Typer, pytest (already in use; no new deps).

**Spec:** [`docs/superpowers/specs/2026-05-11-xdg-base-directories-design.md`](../specs/2026-05-11-xdg-base-directories-design.md)

---

## File Structure

| File | What changes |
|---|---|
| `src/efactura_sync/cli.py` | Add `_xdg_base_dir`, `_xdg_config_home`, `_xdg_data_home` helpers; rewrite `_DEFAULT_CONFIG_DIR`; add `_DEFAULT_DATA_DIR`; rewrite `_OPT_DB` default. |
| `tests/test_cli.py` | Add unit tests for `_xdg_base_dir` (four cases: unset, empty, relative, absolute). |
| `README.md` | Add a "Configuration paths" subsection between Setup and Onboard. |

No new files. No deletions. The helpers and tests live alongside existing code in `cli.py` / `test_cli.py` because they're tightly coupled to CLI default-path resolution and there's no scope creep here that warrants a new module.

---

## Task 1: Add the XDG helpers (TDD)

**Files:**
- Modify: `src/efactura_sync/cli.py` (add helpers near top of module, before `_DEFAULT_CONFIG_DIR`)
- Test: `tests/test_cli.py` (append new test class at end of file)

- [x] **Step 1.1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
# --- XDG base directory helpers ------------------------------------------


class TestXdgBaseDir:
    """Unit tests for the XDG base-directory resolution helper."""

    def test_unset_env_returns_home_fallback(self, monkeypatch, tmp_path) -> None:
        from efactura_sync.cli import _xdg_base_dir

        monkeypatch.delenv("FAKE_XDG_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        result = _xdg_base_dir("FAKE_XDG_HOME", (".config",))

        assert result == tmp_path / ".config"

    def test_empty_env_returns_home_fallback(self, monkeypatch, tmp_path) -> None:
        from efactura_sync.cli import _xdg_base_dir

        monkeypatch.setenv("FAKE_XDG_HOME", "")
        monkeypatch.setenv("HOME", str(tmp_path))

        result = _xdg_base_dir("FAKE_XDG_HOME", (".local", "share"))

        assert result == tmp_path / ".local" / "share"

    def test_relative_env_is_ignored_per_spec(self, monkeypatch, tmp_path) -> None:
        """Per XDG spec: relative paths MUST be ignored."""
        from efactura_sync.cli import _xdg_base_dir

        monkeypatch.setenv("FAKE_XDG_HOME", "relative/not-absolute")
        monkeypatch.setenv("HOME", str(tmp_path))

        result = _xdg_base_dir("FAKE_XDG_HOME", (".config",))

        assert result == tmp_path / ".config"

    def test_absolute_env_is_used_verbatim(self, monkeypatch, tmp_path) -> None:
        from efactura_sync.cli import _xdg_base_dir

        target = tmp_path / "custom" / "xdg"
        monkeypatch.setenv("FAKE_XDG_HOME", str(target))

        result = _xdg_base_dir("FAKE_XDG_HOME", (".config",))

        assert result == target
```

- [x] **Step 1.2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py::TestXdgBaseDir -v`
Expected: 4 failures, all with `ImportError: cannot import name '_xdg_base_dir' from 'efactura_sync.cli'`.

- [x] **Step 1.3: Add `os` to imports if not present**

Open `src/efactura_sync/cli.py` and confirm the top-of-file imports include `import os`. (They don't currently — `socket`, `sqlite3`, `traceback`, `datetime`, `dataclass`, `pathlib`, `typing` are present.)

Add `import os` to the alphabetised stdlib imports block at the top of `cli.py`:

```python
import os
import socket
import sqlite3
import traceback
```

- [x] **Step 1.4: Add the three helpers**

Insert this block in `src/efactura_sync/cli.py` immediately **before** the line `_DEFAULT_CONFIG_DIR = Path.home() / ".config" / "efactura-sync"` (currently around line 52):

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


```

- [x] **Step 1.5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py::TestXdgBaseDir -v`
Expected: 4 passes.

- [x] **Step 1.6: Run full test suite, lint, and type-check**

Run: `.venv/bin/ruff check . && .venv/bin/mypy src && .venv/bin/pytest -q`
Expected: all green, full pre-existing suite still passing.

- [x] **Step 1.7: Commit**

```bash
git add src/efactura_sync/cli.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): add XDG base directory resolution helpers

Pure helpers _xdg_base_dir / _xdg_config_home / _xdg_data_home resolve
the XDG_CONFIG_HOME / XDG_DATA_HOME env vars per the XDG Base Directory
Specification, including the rule that relative values MUST be ignored.

Helpers are not wired into the CLI defaults yet (next commit).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Wire the helpers into the CLI defaults

**Files:**
- Modify: `src/efactura_sync/cli.py:52` (`_DEFAULT_CONFIG_DIR` and `_OPT_DB` definition)

- [x] **Step 2.1: Replace `_DEFAULT_CONFIG_DIR` and `_OPT_DB` default**

In `src/efactura_sync/cli.py`, find this block (currently around lines 52-72):

```python
_DEFAULT_CONFIG_DIR = Path.home() / ".config" / "efactura-sync"
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
    Path.home() / ".local" / "share" / "efactura-sync" / "state.db",
    "--db",
    help="Path to SQLite state database.",
)
```

Replace it with:

```python
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

The three intermediate `typer.Option` definitions (`_OPT_CONFIG`, `_OPT_SECRETS`, `_OPT_TOKENS_DIR`) are unchanged in shape; only `_DEFAULT_CONFIG_DIR`'s right-hand side and `_OPT_DB`'s default value change, and a new `_DEFAULT_DATA_DIR` is introduced.

- [x] **Step 2.2: Add an integration-style test for the default with XDG env unset**

This test exercises the wired-in defaults to make sure they fall through correctly when the env vars are unset, locking in the backwards-compatible behavior for pre-existing installs.

Append to `tests/test_cli.py` inside the `TestXdgBaseDir` class (so the env-isolation pattern is consistent):

```python
    def test_default_config_dir_matches_fallback_when_unset(
        self, monkeypatch, tmp_path
    ) -> None:
        """When XDG_CONFIG_HOME is unset, _xdg_config_home() returns ~/.config."""
        from efactura_sync.cli import _xdg_config_home

        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        assert _xdg_config_home() == tmp_path / ".config"

    def test_default_data_dir_matches_fallback_when_unset(
        self, monkeypatch, tmp_path
    ) -> None:
        """When XDG_DATA_HOME is unset, _xdg_data_home() returns ~/.local/share."""
        from efactura_sync.cli import _xdg_data_home

        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        assert _xdg_data_home() == tmp_path / ".local" / "share"
```

- [x] **Step 2.3: Run tests, lint, type-check**

Run: `.venv/bin/ruff check . && .venv/bin/mypy src && .venv/bin/pytest -q`
Expected: all green; the new tests pass; no regressions in the rest of the suite.

- [x] **Step 2.4: Commit**

```bash
git add src/efactura_sync/cli.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): honor XDG_CONFIG_HOME and XDG_DATA_HOME for default paths

_DEFAULT_CONFIG_DIR and the --db default now derive from the XDG helpers
instead of hardcoded ~/.config and ~/.local/share. Pre-existing installs
keep working unchanged because the env vars are unset there and the
helpers fall back to the same paths.

The --config / --secrets / --tokens-dir / --db override flags are
unchanged.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: README "Configuration paths" subsection

**Files:**
- Modify: `README.md` (insert between `## Setup` and `## Onboard a CUI`)

- [x] **Step 3.1: Identify the insertion point**

Open `README.md`. Locate the start of the `## Onboard a CUI` section (currently around line 61). The new subsection is inserted **immediately above** that line and **below** the last paragraph of `## Setup`.

- [x] **Step 3.2: Insert the new section**

Insert exactly this content immediately above the `## Onboard a CUI` line:

```markdown
## Configuration paths

By default, `efactura-sync` follows the [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html):

| What | Default location |
|---|---|
| `config.toml`, `secrets.toml`, `tokens/` | `${XDG_CONFIG_HOME:-$HOME/.config}/efactura-sync/` |
| `state.db` | `${XDG_DATA_HOME:-$HOME/.local/share}/efactura-sync/` |

To put configuration somewhere else, set the relevant env var before running the CLI:

```bash
XDG_CONFIG_HOME="$HOME/.dotfiles/config" uv run efactura-sync status
```

Per the XDG spec, the env var must be an **absolute** path; empty or relative values are ignored and the `$HOME`-based fallback is used.

For one-off overrides, the following flags take precedence over the XDG defaults on every command:

- `--config <path>` — path to `config.toml`
- `--secrets <path>` — path to `secrets.toml`
- `--tokens-dir <path>` — directory holding per-CUI OAuth token files
- `--db <path>` — path to the SQLite state database

```

(Note: the trailing blank line above is intentional — it separates this subsection from `## Onboard a CUI`.)

- [x] **Step 3.3: Sanity-check the README renders**

Run: `head -100 README.md | tail -50`
Expected: the new section appears between the end of `## Setup` and the start of `## Onboard a CUI`, with the env-var example fenced as a bash block.

- [x] **Step 3.4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): document XDG_CONFIG_HOME / XDG_DATA_HOME and override flags

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Final verification

**Files:** none (verification only).

- [x] **Step 4.1: Full check**

Run:

```bash
.venv/bin/ruff check . && .venv/bin/mypy src && .venv/bin/pytest -q
```

Expected:
- `ruff`: "All checks passed!"
- `mypy`: "Success: no issues found in 16 source files"
- `pytest`: full pre-existing suite plus the six new tests in `TestXdgBaseDir`, all green.

- [x] **Step 4.2: Smoke-test the CLI defaults**

Run:

```bash
XDG_CONFIG_HOME=/tmp/xdg-smoke-test .venv/bin/efactura-sync --help
```

Expected: the help text shows the four path flags with their defaults; defaults reflect the env var when set (e.g., `--config` shows `/tmp/xdg-smoke-test/efactura-sync/config.toml`).

Then:

```bash
unset XDG_CONFIG_HOME XDG_DATA_HOME
.venv/bin/efactura-sync --help
```

Expected: defaults fall back to `$HOME/.config/efactura-sync/...` and `$HOME/.local/share/efactura-sync/state.db`.

- [x] **Step 4.3: Confirm git log is clean**

Run: `git log --oneline -5`
Expected: three new commits on top — helpers, wiring, README — in that order.

---

## Self-Review Notes

- **Spec coverage:**
  - Helpers (`_xdg_base_dir`, `_xdg_config_home`, `_xdg_data_home`) → Task 1.
  - `_DEFAULT_CONFIG_DIR` and `_DEFAULT_DATA_DIR` → Task 2.
  - `_OPT_DB` default switch → Task 2.
  - Unit tests (unset/empty/relative/absolute) → Task 1.
  - README "Configuration paths" subsection → Task 3.
  - Done-criteria checklist items map 1-to-1 to Tasks 1–4.
- **Placeholders:** none — every step has the exact code or command.
- **Type consistency:** `_xdg_base_dir` signature (`env_var: str, fallback_subpath: tuple[str, ...]) -> Path`) is consistent across helper definition, both call sites, and all four test cases.
- **No new dependencies, no new modules, no new test files.**
