from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from efactura_sync.anaf.oauth import (
    Token,
    load_token,
    needs_refresh,
    refresh_access_token,
    save_token,
)
from efactura_sync.errors import RefreshTokenExpired


def _token(**overrides: object) -> Token:
    base = Token(
        cui="12345678",
        env="prod",
        access_token="acc",
        refresh_token="ref",
        expires_at=datetime(2026, 8, 1, tzinfo=UTC),
        obtained_at=datetime(2026, 5, 4, tzinfo=UTC),
    )
    return Token(**{**base.__dict__, **overrides})


def test_save_and_load_token_round_trip(tmp_path: Path) -> None:
    save_token(tmp_path, _token())
    got = load_token(tmp_path, cui="12345678", env="prod")
    assert got == _token()


def test_save_token_chmod_0600(tmp_path: Path) -> None:
    save_token(tmp_path, _token())
    f = tmp_path / "12345678.prod.json"
    mode = f.stat().st_mode & 0o777
    assert mode == 0o600


def test_needs_refresh_window() -> None:
    now = datetime(2026, 7, 25, tzinfo=UTC)  # 7 days before 2026-08-01
    assert needs_refresh(_token(), now=now, buffer_days=7) is True
    earlier = datetime(2026, 7, 20, tzinfo=UTC)  # 12 days before
    assert needs_refresh(_token(), now=earlier, buffer_days=7) is False


def test_refresh_access_token_success() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode() if request.content else ""
        return httpx.Response(
            200,
            json={
                "access_token": "new-acc",
                "refresh_token": "new-ref",
                "expires_in": 7776000,  # 90 days in seconds
                "token_type": "bearer",
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    now = datetime(2026, 5, 4, tzinfo=UTC)
    result = refresh_access_token(
        http=http,
        env="prod",
        client_id="cid",
        client_secret="cs",
        cui="12345678",
        refresh_token="old-ref",
        now=now,
    )

    assert result.access_token == "new-acc"
    assert result.refresh_token == "new-ref"
    assert result.cui == "12345678"
    assert result.env == "prod"
    assert result.obtained_at == now
    assert result.expires_at == now + timedelta(seconds=7776000)
    body = str(captured["body"])
    assert "client_id=cid" in body
    assert "refresh_token=old-ref" in body
    assert "grant_type=refresh_token" in body


def test_refresh_token_400_raises_refresh_expired() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RefreshTokenExpired):
        refresh_access_token(
            http=http,
            env="prod",
            client_id="cid",
            client_secret="cs",
            cui="12345678",
            refresh_token="dead",
            now=datetime(2026, 5, 4, tzinfo=UTC),
        )
