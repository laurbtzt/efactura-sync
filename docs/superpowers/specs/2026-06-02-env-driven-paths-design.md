# Environment-Driven Paths Design

## Goal

Make every filesystem path the CLI uses — the SQLite database, the config
files, the OAuth tokens directory, and the archive root — resolve **only**
from environment variables. Remove every other path mechanism the codebase
currently carries: the `--config/--secrets/--tokens-dir/--db` CLI flags, the
XDG Base Directory resolution, the `platformdirs` fallback, the
`[archive].root` option in `config.toml`, and all `$HOME`/cwd fallbacks.

This supersedes the XDG Base Directory support added on 2026-05-11.

## Background

Today three different path mechanisms are layered together:

- **CLI flags** `--config`, `--secrets`, `--tokens-dir`, `--db` on the
  top-level `@app.callback()` in `cli.py`.
- **XDG env + `$HOME` fallback** (`_xdg_base_dir`, `_xdg_config_home`,
  `_xdg_data_home`, `_DEFAULT_CONFIG_DIR`, `_DEFAULT_DATA_DIR`) that compute the
  flag defaults.
- **`config.toml` `[archive].root`** (with `os.path.expandvars`) plus a
  `platformdirs.user_data_path` fallback, resolved in `config.py` and surfaced
  as `Config.archive_root`.

The result is three ways to set a path and several fallback layers. The new
model replaces all of it with a single, explicit, env-only scheme.

## Environment variables

| Variable | Required | Resolves |
|---|---|---|
| `EFACTURA_SYNC_CONFIG_DIR` | yes | base directory for `config.toml`, `secrets.toml`, and the defaults below |
| `EFACTURA_SYNC_ARCHIVE_DIR` | yes | archive root |
| `EFACTURA_SYNC_DB` | optional override | path to `state.db` (default `<config-dir>/state.db`) |
| `EFACTURA_SYNC_TOKENS_DIR` | optional override | per-CUI OAuth tokens directory (default `<config-dir>/tokens`) |

Resolution rules:

- `config.toml` is always `<config-dir>/config.toml`. **Not** overridable.
- `secrets.toml` is always `<config-dir>/secrets.toml`. **Not** overridable.
- `tokens_dir` is `EFACTURA_SYNC_TOKENS_DIR` if set and non-empty, else
  `<config-dir>/tokens`.
- `db_path` is `EFACTURA_SYNC_DB` if set and non-empty, else
  `<config-dir>/state.db`.
- `archive_dir` is `EFACTURA_SYNC_ARCHIVE_DIR`.

A variable is treated as "set" only if present **and non-empty**; an empty
string counts as unset (so an override that is exported but blank falls back to
the derived default, and a blank required base is an error).

There is **no** absolute-path requirement (unlike the XDG spec it replaces) and
**no** `$HOME`, XDG, `platformdirs`, or current-working-directory fallback of any
kind. Values are used verbatim as `Path(...)`.

### Missing required variables

If either required base variable is unset or empty, the CLI exits with code `2`
and a message naming the first missing variable, e.g.:

```
$ efactura-sync sync run
error: EFACTURA_SYNC_CONFIG_DIR is not set
```

Both bases are required for **every** command (eager resolution in the
top-level callback), not just the commands that happen to use them. This is the
deliberate choice: the deployment sets both variables once in its environment /
systemd unit, so requiring both everywhere is predictable. The one accepted
wart is that `auth login` will also demand `EFACTURA_SYNC_ARCHIVE_DIR` even
though it never touches the archive.

## Components

### New: `src/efactura_sync/paths.py`

A small, self-contained module owning all path resolution.

- `class PathConfigError(ConfigError)` — raised when a required base variable is
  missing. Subclassing `ConfigError` keeps it inside the existing error
  hierarchy.
- `@dataclass(frozen=True) class Paths` with fields:
  `config_path`, `secrets_path`, `tokens_dir`, `db_path`, `archive_dir`
  (all `pathlib.Path`).
- `def resolve_paths(environ: Mapping[str, str] = os.environ) -> Paths` — reads
  the variables, applies the rules above, and returns a `Paths`. Raises
  `PathConfigError` naming the first missing required variable
  (`EFACTURA_SYNC_CONFIG_DIR` checked before `EFACTURA_SYNC_ARCHIVE_DIR`).

Taking `environ` as a parameter (defaulting to `os.environ`) makes the resolver
trivially unit-testable without mutating process state.

### Changed: `src/efactura_sync/cli.py`

- Delete `_xdg_base_dir`, `_xdg_config_home`, `_xdg_data_home`,
  `_DEFAULT_CONFIG_DIR`, `_DEFAULT_DATA_DIR`.
- Delete the path options `_OPT_CONFIG`, `_OPT_SECRETS`, `_OPT_TOKENS_DIR`,
  `_OPT_DB` and the four corresponding parameters on `_main`
  (`@app.callback()`).
- The callback now calls `resolve_paths()`, catches `PathConfigError`, prints
  `error: <VAR> is not set` to stderr, and raises `typer.Exit(code=2)`. On
  success it stores the resulting `Paths` on `ctx.obj`.
- Replace the `_CliContext` dataclass with `Paths` (the `paths.py` type carries
  the same four fields plus `archive_dir`). The `_ctx()` accessor returns
  `Paths`.
- In `sync run`, source the archive root from `paths.archive_dir` instead of the
  removed `cfg.archive_root`.

### Changed: `src/efactura_sync/config.py`

- Remove the `archive_root` field from `Config`.
- Remove the `[archive].root` parsing, the `os.path.expandvars` handling, and
  the `from platformdirs import user_data_path` import and its fallback.
- `load_config(config_path, secrets_path)` keeps its signature; it simply no
  longer produces an archive path. Config is now purely SMTP + ANAF + logging.

### Changed: `pyproject.toml`

- Drop the `platformdirs>=4.2` dependency (its only use was the archive
  fallback in `config.py`).

## Data flow

```
process environment
        |
        v
resolve_paths(os.environ)  -->  Paths(config_path, secrets_path,
        |                              tokens_dir, db_path, archive_dir)
        |  (PathConfigError -> exit 2)
        v
@app.callback() stores Paths on ctx.obj
        |
        +--> load_config(config_path, secrets_path) -> Config (SMTP/ANAF/log)
        +--> _open_db(db_path)
        +--> token store reads/writes tokens_dir
        +--> sync run uses archive_dir for FileStore
```

## Error handling

- Missing required base var → `PathConfigError` in `resolve_paths`, translated
  by the callback to a stderr message + `typer.Exit(2)`.
- Empty-string env var → treated as unset (same as missing).
- Override vars (`EFACTURA_SYNC_DB`, `EFACTURA_SYNC_TOKENS_DIR`) are never
  required; absent or empty means "use the derived default".
- Existing config-file errors (missing/invalid `config.toml`/`secrets.toml`)
  continue to surface as `ConfigError` from `load_config`; unchanged.

## Testing

- **New `tests/test_paths.py`** (unit, no `CliRunner`):
  - both bases set → all five paths derive correctly;
  - `EFACTURA_SYNC_DB` override relocates only the db;
  - `EFACTURA_SYNC_TOKENS_DIR` override relocates only the tokens dir;
  - missing `EFACTURA_SYNC_CONFIG_DIR` → `PathConfigError` naming it;
  - missing `EFACTURA_SYNC_ARCHIVE_DIR` → `PathConfigError` naming it;
  - empty-string base → treated as missing;
  - empty-string override → falls back to the derived default.
- **`tests/test_cli.py`**:
  - delete `TestXdgBaseDir`;
  - rewrite the `_cli_args` helper (and every command test that used it) to set
    `EFACTURA_SYNC_CONFIG_DIR` / `EFACTURA_SYNC_ARCHIVE_DIR` (and overrides where
    needed) via `monkeypatch.setenv`, pointing at `tmp_path`, instead of passing
    `--config/--secrets/--tokens-dir/--db` flags;
  - add a test that a command with `EFACTURA_SYNC_CONFIG_DIR` unset exits `2`
    with the expected message.
- **`tests/test_config.py`**: remove the archive-root default/override cases;
  the remaining config tests are unchanged.

## Documentation

`README.md`:

- Replace the "Configuration paths" section (the XDG table and the four
  `--config/--secrets/--tokens-dir/--db` flags) with an env-var table:
  the two required base vars, the two optional overrides, the derived
  `config.toml`/`secrets.toml`/`tokens/`/`state.db` locations, and the
  hard-error-on-missing behavior (exit 2).
- Update the storage-layout note (currently "Override the archive root via
  `[archive].root` in `config.toml`") to point at `EFACTURA_SYNC_ARCHIVE_DIR`.
- Add the required env vars to the server / cron example so the documented
  command actually runs.

## Out of scope

- No change to the archive directory **layout** under the root
  (`storage/layout.py` is untouched).
- No change to config-file **content** or schema beyond removing `[archive]`.
- `config.toml` and `secrets.toml` remain two separate files; they are not
  merged.
