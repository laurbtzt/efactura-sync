"""ANAF OAuth2: token persistence + refresh.

The interactive authorization-code flow lives in :func:`auth_code_login` (added
in a later task). Refresh and load/save are usable on the headless server.
"""

import json
import logging
import os
import secrets as _secrets
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

from efactura_sync import USER_AGENT
from efactura_sync.errors import AuthError, RefreshTokenExpired
from efactura_sync.types import Env

_log = logging.getLogger(__name__)

_TOKEN_URL = "https://logincert.anaf.ro/anaf-oauth2/v1/token"
_AUTHORIZE_URL = "https://logincert.anaf.ro/anaf-oauth2/v1/authorize"


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
    # Mode 0o600 set at open() — preserved by os.replace, no chmod needed.
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(payload, indent=2).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(partial, p)
    # fsync the parent directory so the rename is durable on power loss.
    try:
        dir_fd = os.open(p.parent, os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


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


def build_authorize_url(*, client_id: str, redirect_uri: str) -> tuple[str, str]:
    """Build the ANAF authorize URL and a fresh CSRF ``state``.

    Returns ``(url, state)``. The caller opens ``url`` in a browser with the
    qualified certificate; after auth ANAF redirects to ``redirect_uri`` with
    ``?code=...&state=...``. ``token_content_type=jwt`` is required so ANAF
    issues a JWT access token.
    """
    state = _secrets.token_urlsafe(24)
    url = f"{_AUTHORIZE_URL}?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "token_content_type": "jwt",
        }
    )
    return url, state


def _extract_code_state(redirect_response: str) -> tuple[str, str | None, bool]:
    """Parse a pasted redirect into ``(code, state, is_url)``.

    A full URL (or any string with a query) yields ``is_url=True`` and the
    ``state`` ANAF echoed (or ``None`` if absent). A bare code yields
    ``is_url=False`` and ``state=None`` — there is no state to verify.
    """
    s = redirect_response.strip()
    is_url = "?" in s or s.lower().startswith("http")
    if is_url:
        parsed = urllib.parse.urlparse(s)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [""])[0]
        state_values = params.get("state")
        state = state_values[0] if state_values else None
        return code, state, True
    return s, None, False


def exchange_code(
    *,
    http: httpx.Client,
    env: Env,
    client_id: str,
    client_secret: str,
    cui: str,
    redirect_uri: str,
    redirect_response: str,
    expected_state: str,
    now: datetime,
) -> Token:
    """Exchange a pasted authorization code for a token.

    ``redirect_response`` is either the full URL ANAF redirected to (read from
    the browser address bar) or a bare code. When a URL is pasted, the echoed
    ``state`` MUST equal ``expected_state`` (strict CSRF check).
    """
    code, state, is_url = _extract_code_state(redirect_response)
    if not code:
        raise AuthError("no authorization code found in pasted redirect")
    if is_url and state != expected_state:
        raise AuthError("state mismatch on ANAF redirect")
    resp = http.post(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "token_content_type": "jwt",
        },
        auth=(client_id, client_secret),
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    )
    if resp.status_code != 200:
        snippet = resp.content[:200].decode("utf-8", "replace")
        raise AuthError(f"token exchange failed (HTTP {resp.status_code}): {snippet}")
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
        _TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "token_content_type": "jwt",
        },
        auth=(client_id, client_secret),
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    )
    if resp.status_code == 400:
        try:
            err = (resp.json() or {}).get("error", "")
        except (ValueError, json.JSONDecodeError):
            err = ""
        snippet = resp.content[:200].decode("utf-8", "replace")
        if err == "invalid_grant":
            raise RefreshTokenExpired(f"refresh failed (invalid_grant): {snippet}")
        raise AuthError(f"refresh failed (400, error={err!r}): {snippet}")
    if resp.status_code != 200:
        snippet = resp.content[:200].decode("utf-8", "replace")
        raise AuthError(f"refresh failed (HTTP {resp.status_code}): {snippet}")
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
    """One-shot HTTP handler that captures the OAuth callback.

    Class-level ``code``/``state`` attributes are a single-call communication
    channel back to ``auth_code_login``. NOT safe for concurrent invocations;
    v1 runs synchronously from a CLI on the operator's laptop.
    """

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
    timeout_seconds: int = 300,
) -> Token:
    """Run the OAuth2 authorization-code flow.

    Opens the system browser at ANAF's authorize URL; ANAF prompts for the
    qualified digital certificate; ANAF redirects back to this short-lived
    local HTTP server with ``?code=...&state=...``. The code is exchanged for
    a token at ANAF's ``/token`` endpoint.

    The local callback uses HTTP on 127.0.0.1 per RFC 8252 §7.3 (loopback
    redirect for native apps). The OS prevents off-host traffic on loopback,
    so cleartext is acceptable here.

    Must run on a host with a browser AND the cert plugged in.
    """
    state = _secrets.token_urlsafe(24)
    server = HTTPServer((bind_host, bind_port), _CallbackHandler)
    actual_port = server.server_address[1]
    redirect_uri = f"http://{bind_host}:{actual_port}/callback"

    auth_url = f"{_AUTHORIZE_URL}?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )

    opened = webbrowser.open(auth_url)
    if not opened:
        _log.warning(
            "could not open browser automatically; open this URL manually: %s",
            auth_url,
        )

    server.timeout = timeout_seconds
    try:
        server.handle_request()
    finally:
        server.server_close()

    if _CallbackHandler.code is None and _CallbackHandler.state is None:
        raise AuthError(f"timed out waiting for ANAF callback ({timeout_seconds}s)")
    if not _CallbackHandler.code:
        raise AuthError("no code received from ANAF callback")
    if _CallbackHandler.state != state:
        raise AuthError("state mismatch on ANAF callback")
    code = _CallbackHandler.code
    _CallbackHandler.code = None
    _CallbackHandler.state = None

    resp = http.post(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    )
    if resp.status_code != 200:
        snippet = resp.content[:200].decode("utf-8", "replace")
        raise AuthError(f"token exchange failed (HTTP {resp.status_code}): {snippet}")
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
