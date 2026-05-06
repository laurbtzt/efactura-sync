import threading
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from efactura_sync.anaf.oauth import (
    Token,
    auth_code_login,
    load_token,
    needs_refresh,
    refresh_access_token,
    save_token,
)
from efactura_sync.errors import AuthError, RefreshTokenExpired


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


def test_save_token_uses_atomic_rename(tmp_path: Path) -> None:
    """No `.partial` file should remain after a successful save."""
    save_token(tmp_path, _token())
    assert (tmp_path / "12345678.prod.json").exists()
    assert not (tmp_path / "12345678.prod.json.partial").exists()


def test_load_token_raises_on_invalid_env(tmp_path: Path) -> None:
    p = tmp_path / "12345678.prod.json"
    p.write_text(
        '{"cui":"12345678","env":"prod-fake","access_token":"a",'
        '"refresh_token":"r","expires_at":"2026-08-01T00:00:00Z",'
        '"obtained_at":"2026-05-04T00:00:00Z"}',
        encoding="utf-8",
    )
    with pytest.raises(AuthError, match="invalid env"):
        load_token(tmp_path, cui="12345678", env="prod")


def test_load_token_raises_on_missing_key(tmp_path: Path) -> None:
    p = tmp_path / "12345678.prod.json"
    # Missing 'access_token' — invalid token file.
    p.write_text(
        '{"cui":"12345678","env":"prod","refresh_token":"r",'
        '"expires_at":"2026-08-01T00:00:00Z","obtained_at":"2026-05-04T00:00:00Z"}',
        encoding="utf-8",
    )
    with pytest.raises(AuthError, match="corrupt token file"):
        load_token(tmp_path, cui="12345678", env="prod")


def test_auth_code_login_full_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate the browser hitting the local callback server with a code."""
    captured_post: dict[str, object] = {}

    def post_handler(request: httpx.Request) -> httpx.Response:
        captured_post["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "access_token": "acc",
                "refresh_token": "ref",
                "expires_in": 7776000,
                "token_type": "bearer",
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(post_handler))

    # Stub webbrowser.open: hit the redirect_uri ourselves with a fake code.
    def fake_open(url: str) -> bool:
        import urllib.parse as up

        q = up.parse_qs(up.urlparse(url).query)
        redirect = q["redirect_uri"][0]
        thread = threading.Thread(
            target=lambda: urllib.request.urlopen(
                f"{redirect}?code=fakecode&state={q['state'][0]}"
            ).read()
        )
        thread.start()
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)

    now = datetime(2026, 5, 4, tzinfo=UTC)
    token = auth_code_login(
        http=http,
        env="prod",
        client_id="cid",
        client_secret="cs",
        cui="12345678",
        now=now,
    )
    assert token.access_token == "acc"
    assert token.cui == "12345678"
    body = str(captured_post["body"])
    assert "code=fakecode" in body
    assert "grant_type=authorization_code" in body
