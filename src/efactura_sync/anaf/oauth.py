"""ANAF OAuth2: token persistence + refresh.

The interactive authorization-code flow lives in :func:`auth_code_login` (added
in a later task). Refresh and load/save are usable on the headless server.
"""

import json
import os
import secrets as _secrets
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Literal

import httpx

from efactura_sync import __version__
from efactura_sync.errors import AuthError, RefreshTokenExpired

Env = Literal["prod", "test"]

_TOKEN_URLS: dict[Env, str] = {
    "prod": "https://logincert.anaf.ro/anaf-oauth2/v1/token",
    "test": "https://logincert.anaf.ro/anaf-oauth2/v1/token",
}

_AUTHORIZE_URLS: dict[Env, str] = {
    "prod": "https://logincert.anaf.ro/anaf-oauth2/v1/authorize",
    "test": "https://logincert.anaf.ro/anaf-oauth2/v1/authorize",
}

_USER_AGENT = f"efactura-sync/{__version__}"


@dataclass(frozen=True)
class Token:
    cui: str
    env: Env
    access_token: str
    refresh_token: str
    expires_at: datetime
    obtained_at: datetime


def _path(tokens_dir: Path, *, cui: str, env: str) -> Path:
    return tokens_dir / f"{cui}.{env}.json"


def save_token(tokens_dir: Path, token: Token) -> None:
    tokens_dir.mkdir(parents=True, exist_ok=True)
    p = _path(tokens_dir, cui=token.cui, env=token.env)
    payload = {
        **asdict(token),
        "expires_at": token.expires_at.isoformat().replace("+00:00", "Z"),
        "obtained_at": token.obtained_at.isoformat().replace("+00:00", "Z"),
    }
    partial = p.with_name(p.name + ".partial")
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(payload, indent=2).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(partial, p)
    os.chmod(p, 0o600)


def load_token(tokens_dir: Path, *, cui: str, env: str) -> Token:
    p = _path(tokens_dir, cui=cui, env=env)
    if not p.exists():
        raise AuthError(f"no token file for cui={cui} env={env}")
    raw = json.loads(p.read_text(encoding="utf-8"))

    def _parse(s: str) -> datetime:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)

    try:
        env_value = raw["env"]
        if env_value not in ("prod", "test"):
            raise AuthError(f"invalid env in token file: {env_value!r}")
        return Token(
            cui=raw["cui"],
            env=env_value,
            access_token=raw["access_token"],
            refresh_token=raw["refresh_token"],
            expires_at=_parse(raw["expires_at"]),
            obtained_at=_parse(raw["obtained_at"]),
        )
    except KeyError as e:
        raise AuthError(f"corrupt token file (missing {e}): {p}") from e


def needs_refresh(token: Token, *, now: datetime, buffer_days: int = 7) -> bool:
    return token.expires_at - now <= timedelta(days=buffer_days)


def refresh_access_token(
    *,
    http: httpx.Client,
    env: Env,
    client_id: str,
    client_secret: str,
    cui: str,
    refresh_token: str,
    now: datetime,
) -> Token:
    resp = http.post(
        _TOKEN_URLS[env],
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"User-Agent": _USER_AGENT},
        timeout=30.0,
    )
    if resp.status_code == 400:
        raise RefreshTokenExpired(f"refresh failed (400): {resp.text[:200]}")
    if resp.status_code != 200:
        raise AuthError(f"refresh failed (HTTP {resp.status_code}): {resp.text[:200]}")
    body = resp.json()
    expires_in = int(body.get("expires_in", 0))
    return Token(
        cui=cui,
        env=env,
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token", refresh_token),
        expires_at=now + timedelta(seconds=expires_in),
        obtained_at=now,
    )


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code_values = params.get("code")
        state_values = params.get("state")
        type(self).code = code_values[0] if code_values else None
        type(self).state = state_values[0] if state_values else None
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<html><body>OK. You can close this tab.</body></html>")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return  # silence access logs in tests


def auth_code_login(
    *,
    http: httpx.Client,
    env: Env,
    client_id: str,
    client_secret: str,
    cui: str,
    now: datetime,
    bind_host: str = "127.0.0.1",
    bind_port: int = 0,
) -> Token:
    """Run the OAuth2 authorization-code flow.

    Opens the system browser at ANAF's authorize URL; ANAF prompts for the
    qualified digital certificate; ANAF redirects back to this short-lived
    local HTTP server with ``?code=...&state=...``. The code is exchanged for
    a token at ANAF's ``/token`` endpoint.

    Must run on a host with a browser AND the cert plugged in.
    """
    state = _secrets.token_urlsafe(24)
    server = HTTPServer((bind_host, bind_port), _CallbackHandler)
    actual_port = server.server_address[1]
    redirect_uri = f"http://{bind_host}:{actual_port}/callback"

    auth_url = f"{_AUTHORIZE_URLS[env]}?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )

    webbrowser.open(auth_url)

    try:
        # serve exactly one request (the callback)
        server.handle_request()
    finally:
        server.server_close()

    if not _CallbackHandler.code:
        raise AuthError("no code received from ANAF callback")
    if _CallbackHandler.state != state:
        raise AuthError("state mismatch on ANAF callback")
    code = _CallbackHandler.code
    _CallbackHandler.code = None
    _CallbackHandler.state = None

    resp = http.post(
        _TOKEN_URLS[env],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"User-Agent": _USER_AGENT},
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise AuthError(f"token exchange failed (HTTP {resp.status_code}): {resp.text[:200]}")
    body = resp.json()
    expires_in = int(body.get("expires_in", 0))
    return Token(
        cui=cui,
        env=env,
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        expires_at=now + timedelta(seconds=expires_in),
        obtained_at=now,
    )
