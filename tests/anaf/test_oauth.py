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
        captured["auth"] = request.headers.get("authorization", "")
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
    assert "refresh_token=old-ref" in body
    assert "grant_type=refresh_token" in body
    assert "token_content_type=jwt" in body
    assert "client_secret" not in body  # creds now in the Basic Auth header
    assert str(captured["auth"]).startswith("Basic ")


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


def test_refresh_token_400_other_error_raises_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_request"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(AuthError, match="invalid_request") as exc_info:
        refresh_access_token(
            http=http,
            env="prod",
            client_id="cid",
            client_secret="cs",
            cui="12345678",
            refresh_token="something",
            now=datetime(2026, 5, 4, tzinfo=UTC),
        )
    # Confirm we did NOT raise the more-specific subclass:
    assert not isinstance(exc_info.value, RefreshTokenExpired)


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


def test_build_authorize_url_contains_required_params() -> None:
    import urllib.parse as up

    from efactura_sync.anaf.oauth import build_authorize_url

    url, state = build_authorize_url(
        client_id="cid", redirect_uri="https://example.com/cb"
    )
    assert state  # non-empty CSRF token
    q = up.parse_qs(up.urlparse(url).query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["cid"]
    assert q["redirect_uri"] == ["https://example.com/cb"]
    assert q["state"] == [state]
    assert q["token_content_type"] == ["jwt"]
    assert url.startswith("https://logincert.anaf.ro/anaf-oauth2/v1/authorize?")


def _exchange_handler(captured: dict[str, object]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode() if request.content else ""
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            json={
                "access_token": "acc",
                "refresh_token": "ref",
                "expires_in": 7776000,
                "token_type": "bearer",
            },
        )

    return handler


def test_exchange_code_full_url_success() -> None:
    from efactura_sync.anaf.oauth import exchange_code

    captured: dict[str, object] = {}
    http = httpx.Client(transport=httpx.MockTransport(_exchange_handler(captured)))
    now = datetime(2026, 5, 4, tzinfo=UTC)
    token = exchange_code(
        http=http,
        env="prod",
        client_id="cid",
        client_secret="cs",
        cui="12345678",
        redirect_uri="https://example.com/cb",
        redirect_response="https://example.com/cb?code=abc123&state=S1",
        expected_state="S1",
        now=now,
    )
    assert token.access_token == "acc"
    assert token.cui == "12345678"
    assert token.expires_at == now + timedelta(seconds=7776000)
    body = str(captured["body"])
    assert "code=abc123" in body
    assert "grant_type=authorization_code" in body
    assert "token_content_type=jwt" in body
    assert "client_secret" not in body  # creds go in the header, not the body
    assert str(captured["auth"]).startswith("Basic ")


def test_exchange_code_bare_code_success() -> None:
    from efactura_sync.anaf.oauth import exchange_code

    captured: dict[str, object] = {}
    http = httpx.Client(transport=httpx.MockTransport(_exchange_handler(captured)))
    token = exchange_code(
        http=http,
        env="prod",
        client_id="cid",
        client_secret="cs",
        cui="12345678",
        redirect_uri="https://example.com/cb",
        redirect_response="  abc123  ",  # bare code, whitespace trimmed
        expected_state="ignored-because-no-state-in-bare-code",
        now=datetime(2026, 5, 4, tzinfo=UTC),
    )
    assert token.access_token == "acc"
    assert "code=abc123" in str(captured["body"])


def test_exchange_code_state_mismatch_raises() -> None:
    from efactura_sync.anaf.oauth import exchange_code

    http = httpx.Client(transport=httpx.MockTransport(_exchange_handler({})))
    with pytest.raises(AuthError, match="state mismatch"):
        exchange_code(
            http=http,
            env="prod",
            client_id="cid",
            client_secret="cs",
            cui="12345678",
            redirect_uri="https://example.com/cb",
            redirect_response="https://example.com/cb?code=abc&state=WRONG",
            expected_state="EXPECTED",
            now=datetime(2026, 5, 4, tzinfo=UTC),
        )


def test_exchange_code_missing_code_raises() -> None:
    from efactura_sync.anaf.oauth import exchange_code

    http = httpx.Client(transport=httpx.MockTransport(_exchange_handler({})))
    with pytest.raises(AuthError, match="no authorization code"):
        exchange_code(
            http=http,
            env="prod",
            client_id="cid",
            client_secret="cs",
            cui="12345678",
            redirect_uri="https://example.com/cb",
            redirect_response="https://example.com/cb?state=S1",  # no code
            expected_state="S1",
            now=datetime(2026, 5, 4, tzinfo=UTC),
        )
