from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from efactura_sync.cli import app

runner = CliRunner()


def test_top_level_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    # The auth subcommand group should be visible.
    assert "auth" in result.stdout


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
