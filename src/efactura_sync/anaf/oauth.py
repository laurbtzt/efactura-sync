"""ANAF OAuth2: token persistence + refresh + interactive login.

The interactive authorization-code flow is built from :func:`build_authorize_url`
and :func:`exchange_code`: the operator opens the authorize URL in a browser,
completes certificate auth, and pastes the resulting redirect URL back so the
code can be exchanged. Refresh and load/save are usable on the headless server.
"""

import json
import os
import secrets as _secrets
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from efactura_sync import USER_AGENT
from efactura_sync.errors import AuthError, RefreshTokenExpired
from efactura_sync.types import Env

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
    """Parse a pasted redirect into ``(code, state, from_url)``.

    A full URL (one with a scheme or a query string) yields ``from_url=True``
    and the ``state`` ANAF echoed (or ``None`` if the query lacked it). A bare
    code yields ``from_url=False`` and ``state=None`` — there is no state to
    verify. The caller enforces state strictly for URL pastes but allows a bare
    code without one.
    """
    s = redirect_response.strip()
    parsed = urllib.parse.urlparse(s)
    if not (parsed.scheme or parsed.query):
        return s, None, False
    params = urllib.parse.parse_qs(parsed.query)
    code = params.get("code", [""])[0]
    state_values = params.get("state")
    return code, (state_values[0] if state_values else None), True


def _token_from_body(
    body: object,
    *,
    cui: str,
    env: Env,
    now: datetime,
    fallback_refresh_token: str | None = None,
) -> Token:
    """Build a :class:`Token` from a parsed token-endpoint JSON body.

    Raises :class:`AuthError` (never a raw ``KeyError``) when the response is
    not a JSON object or omits required fields. ``fallback_refresh_token`` lets
    the refresh path retain the previous refresh token when ANAF does not return
    a new one.
    """
    if not isinstance(body, dict):
        raise AuthError(f"unexpected token response (not a JSON object): {body!r:.200}")
    access_token = body.get("access_token")
    if not access_token:
        raise AuthError(f"token response missing access_token: {body!r:.200}")
    refresh_token = body.get("refresh_token") or fallback_refresh_token
    if not refresh_token:
        raise AuthError("token response missing refresh_token")
    expires_in = int(body.get("expires_in", 0))
    return Token(
        cui=cui,
        env=env,
        access_token=str(access_token),
        refresh_token=str(refresh_token),
        expires_at=now + timedelta(seconds=expires_in),
        obtained_at=now,
    )


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
    ``state`` MUST equal ``expected_state`` (strict CSRF check); a bare code
    carries no state and is accepted without one.
    """
    code, state, from_url = _extract_code_state(redirect_response)
    if not code:
        raise AuthError("no authorization code found in pasted redirect")
    if from_url and state != expected_state:
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
    return _token_from_body(resp.json(), cui=cui, env=env, now=now)


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
    return _token_from_body(
        resp.json(), cui=cui, env=env, now=now, fallback_refresh_token=refresh_token
    )
