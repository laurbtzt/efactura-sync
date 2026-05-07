import sqlite3 as _sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from efactura_sync.cli import app
from efactura_sync.storage.db import (
    init_schema as _init_schema,
)
from efactura_sync.storage.db import (
    list_monitored_cuis as _list_monitored_cuis,
)
from efactura_sync.storage.db import (
    list_tracked_counterparties as _list_tracked_counterparties,
)

runner = CliRunner()


def _write_fixture_files(tmp_path: Path) -> tuple[Path, Path]:
    """Write minimal valid config.toml + secrets.toml; return their paths."""
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
    return config_file, secrets_file


def test_top_level_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "auth" in result.stdout
    assert "cui" in result.stdout
    assert "track" in result.stdout
    assert "sync" in result.stdout


def test_auth_login_invokes_oauth_and_writes_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    from efactura_sync.anaf.oauth import Token

    def fake_login(**kwargs: object) -> Token:
        captured.update(kwargs)
        return Token(
            cui=str(kwargs["cui"]),
            env=str(kwargs["env"]),  # type: ignore[arg-type]
            access_token="acc",
            refresh_token="ref",
            expires_at=datetime(2026, 8, 1, tzinfo=UTC),
            obtained_at=datetime(2026, 5, 4, tzinfo=UTC),
        )

    monkeypatch.setattr("efactura_sync.cli.auth_code_login", fake_login)

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
    assert result.exit_code == 0, result.stdout
    assert captured["cui"] == "12345678"
    assert captured["env"] == "prod"
    token_file = tmp_path / "tokens" / "12345678.prod.json"
    assert token_file.exists()


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


def test_auth_refresh_loads_and_writes_new_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from efactura_sync.anaf.oauth import Token
    from efactura_sync.anaf.oauth import save_token as real_save_token

    # Pre-write an existing token to disk.
    tokens_dir = tmp_path / "tokens"
    real_save_token(
        tokens_dir,
        Token(
            cui="12345678",
            env="prod",
            access_token="old-acc",
            refresh_token="old-ref",
            expires_at=datetime(2026, 8, 1, tzinfo=UTC),
            obtained_at=datetime(2026, 5, 4, tzinfo=UTC),
        ),
    )

    captured: dict[str, object] = {}

    def fake_refresh(**kwargs: object) -> Token:
        captured.update(kwargs)
        return Token(
            cui="12345678",
            env="prod",
            access_token="new-acc",
            refresh_token="new-ref",
            expires_at=datetime(2026, 11, 1, tzinfo=UTC),
            obtained_at=datetime(2026, 8, 1, tzinfo=UTC),
        )

    monkeypatch.setattr("efactura_sync.cli.refresh_access_token", fake_refresh)

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
    assert result.exit_code == 0, result.stdout
    # The fake_refresh got called with the existing refresh_token.
    assert captured["refresh_token"] == "old-ref"
    # New token persisted with new access_token.
    from efactura_sync.anaf.oauth import load_token

    persisted = load_token(tokens_dir, cui="12345678", env="prod")
    assert persisted.access_token == "new-acc"
    assert persisted.refresh_token == "new-ref"


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


def test_auth_login_propagates_oauth_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from efactura_sync.errors import AuthError

    def fake_login_failing(**kwargs: object) -> object:
        raise AuthError("simulated oauth failure")

    monkeypatch.setattr("efactura_sync.cli.auth_code_login", fake_login_failing)

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
    assert result.exit_code != 0
    # No token file should have been written.
    assert not (tmp_path / "tokens" / "12345678.prod.json").exists()


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


def test_cui_add_list_remove(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"

    r1 = runner.invoke(
        app, _cli_args(tmp_path, db_path) + ["cui", "add", "12345678", "--name", "Acme"]
    )
    assert r1.exit_code == 0, r1.stdout

    r2 = runner.invoke(app, _cli_args(tmp_path, db_path) + ["cui", "list"])
    assert r2.exit_code == 0, r2.stdout
    assert "12345678" in r2.stdout
    assert "Acme" in r2.stdout

    r3 = runner.invoke(app, _cli_args(tmp_path, db_path) + ["cui", "remove", "12345678"])
    assert r3.exit_code == 0, r3.stdout

    # Verify by re-opening the DB directly: nothing left.
    conn = _sqlite3.connect(db_path)
    _init_schema(conn)
    try:
        assert _list_monitored_cuis(conn) == []
    finally:
        conn.close()


def test_cui_list_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    result = runner.invoke(app, _cli_args(tmp_path, db_path) + ["cui", "list"])
    assert result.exit_code == 0, result.stdout
    assert "no monitored CUIs" in result.stdout


def test_track_add_list_remove(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    # Register the parent monitored CUI first.
    runner.invoke(app, _cli_args(tmp_path, db_path) + ["cui", "add", "12345678"])

    r1 = runner.invoke(
        app,
        _cli_args(tmp_path, db_path) + ["track", "add", "RO111", "--cui", "12345678"],
    )
    assert r1.exit_code == 0, r1.stdout

    r2 = runner.invoke(app, _cli_args(tmp_path, db_path) + ["track", "list", "--cui", "12345678"])
    assert r2.exit_code == 0, r2.stdout
    assert "RO111" in r2.stdout

    r3 = runner.invoke(
        app,
        _cli_args(tmp_path, db_path) + ["track", "remove", "RO111", "--cui", "12345678"],
    )
    assert r3.exit_code == 0, r3.stdout

    conn = _sqlite3.connect(db_path)
    _init_schema(conn)
    try:
        assert _list_tracked_counterparties(conn, my_cui="12345678") == []
    finally:
        conn.close()


def test_track_add_without_parent_cui_fails(tmp_path: Path) -> None:
    """track add requires the parent monitored CUI to exist first (FK constraint)."""
    db_path = tmp_path / "state.db"
    result = runner.invoke(
        app,
        _cli_args(tmp_path, db_path)
        + ["track", "add", "RO111", "--cui", "12345678"],  # 12345678 not added
    )
    assert result.exit_code != 0


def test_cui_remove_cascades_to_tracked(tmp_path: Path) -> None:
    """`cui remove` should also remove rows in tracked_counterparties (FK CASCADE)."""
    db_path = tmp_path / "state.db"
    runner.invoke(app, _cli_args(tmp_path, db_path) + ["cui", "add", "12345678"])
    runner.invoke(
        app,
        _cli_args(tmp_path, db_path) + ["track", "add", "RO111", "--cui", "12345678"],
    )

    runner.invoke(app, _cli_args(tmp_path, db_path) + ["cui", "remove", "12345678"])

    conn = _sqlite3.connect(db_path)
    _init_schema(conn)
    try:
        # Both monitored CUI and its tracked counterparty are gone.
        assert _list_monitored_cuis(conn) == []
        assert _list_tracked_counterparties(conn, my_cui="12345678") == []
    finally:
        conn.close()
