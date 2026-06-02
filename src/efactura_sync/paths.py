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
