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
