# XDG Base Directory Support

**Date:** 2026-05-11
**Status:** Design approved
**Author:** brainstorming session

## Goal

Honor the [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html) for both the config directory and the state-database directory. The existing CLI path-override flags remain unchanged.

## Motivation

The project's own design doc (`docs/superpowers/specs/2026-05-04-efactura-sync-design.md`, §5.1) already documents the intended layout in terms of `$XDG_CONFIG_HOME/efactura-sync/` and `$XDG_DATA_HOME/efactura-sync/`. The current implementation hardcodes `~/.config/efactura-sync/` and `~/.local/share/efactura-sync/`, so a user who sets `XDG_CONFIG_HOME=$HOME/.dotfiles/config` (a common dotfiles-repo pattern) has no way to point the CLI at it short of passing `--config`/`--secrets`/`--tokens-dir` on every invocation.

## Scope

In scope:
- `cli.py` only: replace two hardcoded base-path expressions.
- README: short "Configuration paths" section.
- Unit tests for the two new XDG helpers.

Out of scope:
- New CLI flag (e.g. `--config-dir`). Rejected during brainstorming in favor of the env-var-only approach.
- `XDG_CACHE_HOME`. No cache files in the project yet.
- Changes to `archive.root`, which is already routed through `platformdirs.user_data_path` and is independently overridable in `config.toml`.

## Behavior

### Default base paths

| What | New default expression |
|---|---|
| Config dir (parent of `config.toml`, `secrets.toml`, `tokens/`) | `${XDG_CONFIG_HOME:-$HOME/.config}/efactura-sync/` |
| State DB parent dir | `${XDG_DATA_HOME:-$HOME/.local/share}/efactura-sync/` |

### Fallback rules (per XDG spec)

A `$HOME`-based fallback is used when the env var is:
1. **Unset.**
2. **Empty string.** Treated as unset, per common shell semantics.
3. **Not absolute.** The XDG spec is explicit: "If [the value] is set to a relative path, the value MUST be ignored."

When the env var is set and absolute, that path is used verbatim. No expansion of `~` is performed (the spec says nothing about it, and POSIX shells expand `~` before the program sees the env var anyway).

### CLI override flags

All four existing options keep their current semantics — they override the derived default when passed:
- `--config <path>` overrides the path to `config.toml`.
- `--secrets <path>` overrides the path to `secrets.toml`.
- `--tokens-dir <path>` overrides the tokens directory.
- `--db <path>` overrides the state-DB path.

The XDG env vars only affect the *defaults* for these flags.

## Implementation

### Helpers (in `src/efactura_sync/cli.py`)

```python
def _xdg_base_dir(env_var: str, fallback_subpath: tuple[str, ...]) -> Path:
    """Resolve an XDG base directory per the spec.

    Returns ``$<env_var>`` if it is set, non-empty, and absolute.
    Otherwise returns ``Path.home() / <fallback_subpath>``.
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

### Wiring

```python
_DEFAULT_CONFIG_DIR = _xdg_config_home() / "efactura-sync"
_DEFAULT_DATA_DIR = _xdg_data_home() / "efactura-sync"
```

Replace the two existing constant expressions and the `_OPT_DB` default with these:

```python
_OPT_DB = typer.Option(
    _DEFAULT_DATA_DIR / "state.db",
    "--db",
    help="Path to SQLite state database.",
)
```

The three `_OPT_CONFIG`, `_OPT_SECRETS`, `_OPT_TOKENS_DIR` options keep their current form, now reading from the new `_DEFAULT_CONFIG_DIR`.

### Module-load timing

The defaults resolve once at import time (matching the current behavior). The CLI is short-lived and `os.environ` does not change during a run, so this is fine.

## Tests

`tests/test_cli.py` — direct unit tests on `_xdg_base_dir`:

| Case | env var value | Expected |
|---|---|---|
| Unset | env var deleted | `Path.home() / <fallback>` |
| Empty | `""` | `Path.home() / <fallback>` |
| Relative | `"relative/path"` | `Path.home() / <fallback>` (per spec) |
| Absolute | `"/tmp/x"` | `Path("/tmp/x")` |

Tests use `monkeypatch.setenv` / `monkeypatch.delenv` for env isolation; no real filesystem writes needed because the helper is pure.

No changes to existing integration tests — they don't depend on the default values.

## README

Add a short subsection (after the existing setup section, before "Daily run") titled **"Configuration paths"**:

- Documents the two base paths and their `XDG_*` overrides.
- Lists the four CLI flags that override per-file.
- One concrete example: `XDG_CONFIG_HOME=$HOME/.dotfiles/config efactura-sync status`.

## Risks / non-issues

- **Pre-existing installs:** Users with files under `~/.config/efactura-sync/` and `~/.local/share/efactura-sync/` continue to work unchanged — they have `XDG_*` unset, so the fallback returns exactly those paths.
- **Empty-string env var:** Some shells export `XDG_CONFIG_HOME=` for reasons; the empty-string fallback handles this safely.
- **`tomllib`/file-reading still surfaces clear errors:** `load_config` already raises `ConfigError("missing <label> at <path>")` if a file is missing, so a misconfigured `XDG_CONFIG_HOME` produces an actionable error message.

## Done criteria

- [ ] `_xdg_config_home`, `_xdg_data_home`, `_xdg_base_dir` defined in `cli.py`.
- [ ] `_DEFAULT_CONFIG_DIR` and `_DEFAULT_DATA_DIR` derived from them.
- [ ] `_OPT_DB` reads from `_DEFAULT_DATA_DIR`.
- [ ] Four unit tests for `_xdg_base_dir` (unset, empty, relative, absolute).
- [ ] `ruff check`, `mypy --strict src`, `pytest -q` all green.
- [ ] README has a "Configuration paths" subsection.
