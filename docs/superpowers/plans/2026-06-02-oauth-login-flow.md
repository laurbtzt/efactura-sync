# OAuth Login Flow Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `efactura-sync auth login` work against ANAF by replacing the broken local-callback-server flow with a configured-`redirect_uri` + manual code-paste flow, and fix the token endpoint to send `token_content_type=jwt` and HTTP Basic Auth.

**Architecture:** ANAF requires a fixed HTTPS `redirect_uri` registered in the OAuth profile and will not accept loopback URLs. The CLI no longer hosts a server: it builds the authorize URL, the operator completes cert auth in a browser, and pastes the resulting redirect URL (or bare code) back; the CLI extracts the code and exchanges it. The redirect target needs no running server (Tier 0) — the code is read from the browser address bar. The token endpoint now authenticates with a Basic Auth header and requests JWTs via `token_content_type=jwt`.

**Tech Stack:** Python 3.14, httpx (with `MockTransport` for tests), Typer CLI, pytest, tomllib.

**Spec:** `docs/superpowers/specs/2026-06-02-oauth-login-flow-design.md`

---

## File Structure

- `src/efactura_sync/config.py` — add per-env `redirect_uri` to `AnafConfig` + parsing + `anaf_redirect_uri()`.
- `src/efactura_sync/anaf/oauth.py` — add `build_authorize_url` + `exchange_code` + `_extract_code_state`; fix `refresh_access_token`; remove `auth_code_login`, `_CallbackHandler`, and now-unused imports.
- `src/efactura_sync/cli.py` — rewire `auth login` to the new functions + `typer.prompt`; import `webbrowser`.
- `tests/test_config.py` — tests for per-env `redirect_uri`.
- `tests/anaf/test_oauth.py` — tests for new functions; update refresh test; delete old `auth_code_login` tests.
- `tests/test_cli.py` — update `auth login` tests + the `_write_fixture_files` config fixture.
- `README.md` — rewrite the onboarding login block.

---

## Task 1: Config — per-env `redirect_uri`

**Files:**
- Modify: `src/efactura_sync/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_load_config_redirect_uri_per_env(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "config.toml",
        """
        [smtp]
        host="h"
        port=465
        tls="implicit"
        from_addr="a"
        to_addr="b"
        [anaf]
        default_env="prod"
        [anaf.prod]
        redirect_uri="https://example.com/cb-prod"
        [anaf.test]
        redirect_uri="https://example.com/cb-test"
        [logging]
        level="INFO"
        """,
    )
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username="u"
        password="p"
        [anaf.prod]
        client_id="x"
        client_secret="y"
        [anaf.test]
        client_id="x"
        client_secret="y"
        """,
    )
    cfg = load_config(config_path=cfg_path, secrets_path=sec_path)
    assert cfg.anaf_redirect_uri("prod") == "https://example.com/cb-prod"
    assert cfg.anaf_redirect_uri("test") == "https://example.com/cb-test"


def test_load_config_redirect_uri_optional(tmp_path: Path) -> None:
    # The minimal config (no [anaf.prod]/[anaf.test] redirect_uri) yields None.
    cfg_path = _write(
        tmp_path,
        "config.toml",
        """
        [smtp]
        host="h"
        port=465
        tls="implicit"
        from_addr="a"
        to_addr="b"
        [anaf]
        default_env="prod"
        [logging]
        level="INFO"
        """,
    )
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username="u"
        password="p"
        [anaf.prod]
        client_id="x"
        client_secret="y"
        [anaf.test]
        client_id="x"
        client_secret="y"
        """,
    )
    cfg = load_config(config_path=cfg_path, secrets_path=sec_path)
    assert cfg.anaf_redirect_uri("prod") is None
    assert cfg.anaf_redirect_uri("test") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_load_config_redirect_uri_per_env tests/test_config.py::test_load_config_redirect_uri_optional -v`
Expected: FAIL — `AnafConfig.__init__() ... unexpected keyword` or `AttributeError: 'Config' object has no attribute 'anaf_redirect_uri'`.

- [ ] **Step 3: Add the fields, parsing, and accessor**

In `src/efactura_sync/config.py`, extend `AnafConfig` (currently ends at `test_client_secret: str`):

```python
@dataclass(frozen=True)
class AnafConfig:
    default_env: Env
    prod_client_id: str
    prod_client_secret: str
    test_client_id: str
    test_client_secret: str
    prod_redirect_uri: str | None
    test_redirect_uri: str | None
```

Add this method to `Config` (right after `anaf_credentials`):

```python
    def anaf_redirect_uri(self, env: Env) -> str | None:
        if env == "prod":
            return self.anaf.prod_redirect_uri
        if env == "test":
            return self.anaf.test_redirect_uri
        raise ConfigError(f"unknown env: {env!r}")
```

In `load_config`, just before the `anaf = AnafConfig(...)` construction, read the optional per-env subtables from `config.toml` (`anaf_cfg` is already `cfg["anaf"]`):

```python
    anaf_prod_cfg = anaf_cfg.get("prod", {})
    anaf_test_cfg = anaf_cfg.get("test", {})
    prod_redirect_uri = anaf_prod_cfg.get("redirect_uri")
    test_redirect_uri = anaf_test_cfg.get("redirect_uri")
```

Then extend the `AnafConfig(...)` call with:

```python
        prod_redirect_uri=str(prod_redirect_uri) if prod_redirect_uri is not None else None,
        test_redirect_uri=str(test_redirect_uri) if test_redirect_uri is not None else None,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all config tests, including the two new ones).

- [ ] **Step 5: Commit**

```bash
git add src/efactura_sync/config.py tests/test_config.py
git commit -m "feat(config): per-env anaf redirect_uri from config.toml"
```

---

## Task 2: `build_authorize_url`

**Files:**
- Modify: `src/efactura_sync/anaf/oauth.py`
- Test: `tests/anaf/test_oauth.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/anaf/test_oauth.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/anaf/test_oauth.py::test_build_authorize_url_contains_required_params -v`
Expected: FAIL — `ImportError: cannot import name 'build_authorize_url'`.

- [ ] **Step 3: Implement `build_authorize_url`**

In `src/efactura_sync/anaf/oauth.py`, add (e.g. just after `needs_refresh`):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/anaf/test_oauth.py::test_build_authorize_url_contains_required_params -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/efactura_sync/anaf/oauth.py tests/anaf/test_oauth.py
git commit -m "feat(oauth): build_authorize_url with token_content_type=jwt"
```

---

## Task 3: `exchange_code`

**Files:**
- Modify: `src/efactura_sync/anaf/oauth.py`
- Test: `tests/anaf/test_oauth.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/anaf/test_oauth.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/anaf/test_oauth.py -k exchange_code -v`
Expected: FAIL — `ImportError: cannot import name 'exchange_code'`.

- [ ] **Step 3: Implement `_extract_code_state` and `exchange_code`**

In `src/efactura_sync/anaf/oauth.py`, add after `build_authorize_url`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/anaf/test_oauth.py -k exchange_code -v`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add src/efactura_sync/anaf/oauth.py tests/anaf/test_oauth.py
git commit -m "feat(oauth): exchange_code with manual paste, Basic Auth, strict state"
```

---

## Task 4: Fix `refresh_access_token` (Basic Auth + JWT)

**Files:**
- Modify: `src/efactura_sync/anaf/oauth.py:103-145` (the `refresh_access_token` body)
- Test: `tests/anaf/test_oauth.py` (update `test_refresh_access_token_success`)

- [ ] **Step 1: Update the existing test to assert the new wire format**

In `tests/anaf/test_oauth.py`, replace the body of `test_refresh_access_token_success`'s handler and assertions so it captures the auth header and no longer expects creds in the body.

Change the handler (currently only captures `body`) to also capture the header:

```python
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
```

Replace the trailing body assertions (currently `assert "client_id=cid" in body` etc.) with:

```python
    body = str(captured["body"])
    assert "refresh_token=old-ref" in body
    assert "grant_type=refresh_token" in body
    assert "token_content_type=jwt" in body
    assert "client_secret" not in body  # creds now in the Basic Auth header
    assert str(captured["auth"]).startswith("Basic ")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/anaf/test_oauth.py::test_refresh_access_token_success -v`
Expected: FAIL — `client_secret` still present in body / `auth` header empty.

- [ ] **Step 3: Update `refresh_access_token`**

In `src/efactura_sync/anaf/oauth.py`, change the `http.post(...)` call inside `refresh_access_token` so credentials move to `auth=` and the body gains `token_content_type=jwt`:

```python
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
```

(Leave the `client_id`/`client_secret` parameters in the function signature — they now feed `auth=`. The 400/`invalid_grant` handling below is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/anaf/test_oauth.py -k refresh -v`
Expected: PASS (success test plus the two 400-path tests still green).

- [ ] **Step 5: Commit**

```bash
git add src/efactura_sync/anaf/oauth.py tests/anaf/test_oauth.py
git commit -m "fix(oauth): refresh uses Basic Auth header and requests JWT"
```

---

## Task 5: Rewire CLI `auth login` to the paste flow

**Files:**
- Modify: `src/efactura_sync/cli.py` (imports + `auth_login` at `:120-138`)
- Test: `tests/test_cli.py` (update `_write_fixture_files` + the `auth login` tests)

- [ ] **Step 1: Update the CLI tests + fixture**

In `tests/test_cli.py`, add a `redirect_uri` to both env subtables in `_write_fixture_files` so `config.toml` becomes:

```python
    config_file.write_text(
        "[smtp]\nhost='h'\nport=465\ntls='implicit'\nfrom_addr='a'\nto_addr='b'\n"
        "[anaf]\ndefault_env='prod'\n"
        "[anaf.prod]\nredirect_uri='https://example.com/cb'\n"
        "[anaf.test]\nredirect_uri='https://example.com/cb'\n"
        "[logging]\nlevel='INFO'\n",
        encoding="utf-8",
    )
```

Replace `test_auth_login_invokes_oauth_and_writes_token` with a version that monkeypatches the two new functions, stubs the browser, and feeds the paste via `input=`:

```python
def test_auth_login_invokes_oauth_and_writes_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    from efactura_sync.anaf.oauth import Token

    def fake_build(**kwargs: object) -> tuple[str, str]:
        captured.update(kwargs)
        return "https://logincert.anaf.ro/anaf-oauth2/v1/authorize?x=1", "STATE123"

    def fake_exchange(**kwargs: object) -> Token:
        captured["exchange"] = kwargs
        return Token(
            cui=str(kwargs["cui"]),
            env=str(kwargs["env"]),  # type: ignore[arg-type]
            access_token="acc",
            refresh_token="ref",
            expires_at=datetime(2026, 8, 1, tzinfo=UTC),
            obtained_at=datetime(2026, 5, 4, tzinfo=UTC),
        )

    monkeypatch.setattr("efactura_sync.cli.build_authorize_url", fake_build)
    monkeypatch.setattr("efactura_sync.cli.exchange_code", fake_exchange)
    monkeypatch.setattr("efactura_sync.cli.webbrowser.open", lambda _url: True)

    result = runner.invoke(
        app,
        ["auth", "login", "--cui", "12345678", "--env", "prod"],
        input="https://example.com/cb?code=abc&state=STATE123\n",
    )
    assert result.exit_code == 0, result.stdout
    assert captured["client_id"] == "cid"
    exchange_kwargs = captured["exchange"]
    assert exchange_kwargs["expected_state"] == "STATE123"  # type: ignore[index]
    assert exchange_kwargs["redirect_uri"] == "https://example.com/cb"  # type: ignore[index]
    token_file = tmp_path / "tokens" / "12345678.prod.json"
    assert token_file.exists()
```

Replace `test_auth_login_propagates_oauth_failure` with one that fails inside `exchange_code`:

```python
def test_auth_login_propagates_oauth_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from efactura_sync.errors import AuthError

    monkeypatch.setattr(
        "efactura_sync.cli.build_authorize_url",
        lambda **_kw: ("https://authorize?x=1", "S1"),
    )
    monkeypatch.setattr("efactura_sync.cli.webbrowser.open", lambda _url: True)

    def fake_exchange_failing(**_kwargs: object) -> object:
        raise AuthError("simulated oauth failure")

    monkeypatch.setattr("efactura_sync.cli.exchange_code", fake_exchange_failing)

    result = runner.invoke(
        app,
        ["auth", "login", "--cui", "12345678", "--env", "prod"],
        input="https://example.com/cb?code=abc&state=S1\n",
    )
    assert result.exit_code != 0
```

Add a test for the missing-`redirect_uri` guard:

```python
def test_auth_login_missing_redirect_uri_exits_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Rewrite config.toml without any redirect_uri.
    (tmp_path / "config.toml").write_text(
        "[smtp]\nhost='h'\nport=465\ntls='implicit'\nfrom_addr='a'\nto_addr='b'\n"
        "[anaf]\ndefault_env='prod'\n[logging]\nlevel='INFO'\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["auth", "login", "--cui", "12345678", "--env", "prod"],
    )
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "redirect_uri" in combined
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k auth_login -v`
Expected: FAIL — `efactura_sync.cli` has no `build_authorize_url`/`exchange_code` attribute, and `cli.webbrowser` does not exist.

- [ ] **Step 3: Rewire the CLI**

In `src/efactura_sync/cli.py`, add `import webbrowser` near the top imports (after `import traceback`). Change the oauth import block to use the new functions:

```python
from efactura_sync.anaf.oauth import (
    build_authorize_url,
    exchange_code,
    load_token,
    needs_refresh,
    refresh_access_token,
    save_token,
)
```

Replace the entire `auth_login` function body with:

```python
@auth_app.command("login")
def auth_login(
    ctx: typer.Context,
    cui: str = _OPT_CUI_LOGIN,
    env: str = _OPT_ENV,
) -> None:
    """Run the OAuth2 authorization-code flow (paste the redirect URL back)."""
    cli_ctx, cfg, env_typed, client_id, client_secret, now = _prepare(ctx, env)
    redirect_uri = cfg.anaf_redirect_uri(env_typed)
    if not redirect_uri:
        typer.echo(
            f"error: no redirect_uri configured for env '{env_typed}'. "
            f"Add it under [anaf.{env_typed}] in config.toml.",
            err=True,
        )
        raise typer.Exit(code=2)

    url, state = build_authorize_url(client_id=client_id, redirect_uri=redirect_uri)
    opened = webbrowser.open(url)
    typer.echo("Open this URL in a browser with your ANAF certificate plugged in:")
    typer.echo(f"\n  {url}\n")
    if not opened:
        typer.echo("(could not open the browser automatically — copy the URL above)")
    typer.echo(
        "After certificate auth, your browser is redirected to your callback URL. "
        "The page need not load — copy the full address-bar URL (it contains "
        "?code=...) and paste it below."
    )
    pasted = typer.prompt("Paste the redirect URL (or just the code)")

    with httpx.Client() as http:
        token = exchange_code(
            http=http,
            env=env_typed,
            client_id=client_id,
            client_secret=client_secret,
            cui=cui,
            redirect_uri=redirect_uri,
            redirect_response=pasted,
            expected_state=state,
            now=now,
        )
    save_token(cli_ctx.tokens_dir, token)
    typer.echo(f"OK — token saved; expires {token.expires_at.isoformat()}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (all CLI tests).

- [ ] **Step 5: Commit**

```bash
git add src/efactura_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): auth login uses configured redirect_uri + manual paste"
```

---

## Task 6: Remove the dead local-server flow

**Files:**
- Modify: `src/efactura_sync/anaf/oauth.py` (delete `auth_code_login`, `_CallbackHandler`, unused imports)
- Test: `tests/anaf/test_oauth.py` (delete the two `auth_code_login` tests)

- [ ] **Step 1: Delete the obsolete tests**

In `tests/anaf/test_oauth.py`, delete `test_auth_code_login_full_flow` and `test_auth_code_login_times_out` entirely. Remove `auth_code_login` from the top-level import block (`from efactura_sync.anaf.oauth import (...)`). Remove the now-unused module imports at the top of the test file: `threading` and `urllib.request`.

- [ ] **Step 2: Delete the obsolete implementation**

In `src/efactura_sync/anaf/oauth.py`:
- Delete the `_CallbackHandler` class (`class _CallbackHandler(BaseHTTPRequestHandler): ...`).
- Delete the `auth_code_login` function (the `def auth_code_login(...)` through its `return Token(...)`).
- Remove now-unused imports: `os`? — NO, `os` is still used by `save_token`. Remove only `webbrowser` and the `from http.server import BaseHTTPRequestHandler, HTTPServer` line. Update the module docstring's first paragraph that references `auth_code_login` to describe the paste flow instead.

- [ ] **Step 3: Run the full oauth suite + lint**

Run: `uv run pytest tests/anaf/test_oauth.py -v && uv run ruff check src/efactura_sync/anaf/oauth.py tests/anaf/test_oauth.py`
Expected: PASS, and ruff reports no unused-import (`F401`) errors.

- [ ] **Step 4: Run the whole test suite**

Run: `uv run pytest`
Expected: PASS (entire suite).

- [ ] **Step 5: Commit**

```bash
git add src/efactura_sync/anaf/oauth.py tests/anaf/test_oauth.py
git commit -m "refactor(oauth): remove dead local-callback-server login flow"
```

---

## Task 7: Update README onboarding

**Files:**
- Modify: `README.md` (the "Onboard a CUI" block around `:93-105` and the `secrets.toml`/`config.toml` examples around `:54-69`)

- [ ] **Step 1: Add `redirect_uri` to the config.toml example**

In the `config.toml` example block (the `[anaf]` section currently only has `default_env = "prod"`), add per-env subtables:

```toml
[anaf]
default_env = "prod"

[anaf.prod]
redirect_uri = "https://yourdomain.tld/efactura-callback"

[anaf.test]
redirect_uri = "https://yourdomain.tld/efactura-callback"
```

Add a one-line note after the block:

> `redirect_uri` must exactly match the HTTPS Callback URL registered in your ANAF OAuth profile. It needs no running server — see "Onboard a CUI".

- [ ] **Step 2: Rewrite the "Onboard a CUI" login steps**

Replace the current block (including the `[not working so easy...]` line and the local-browser comment) with:

````markdown
## Onboard a CUI

Register the OAuth app once in the ANAF portal (Servicii Online > Înregistrare
utilizatori > DEZVOLTATORI APLICAȚII) with an **HTTPS** Callback URL on a domain
you control, e.g. `https://yourdomain.tld/efactura-callback`. ANAF rejects
`http://localhost`. **The callback needs no running server** — after cert auth
your browser is redirected there with `?code=...` in the address bar, even if the
page itself 404s. Put that same URL in `config.toml` under `[anaf.<env>]`.

On the **laptop** (digital cert plugged in):

```bash
uv run efactura-sync cui add 12345678 --name "Acme SRL"
uv run efactura-sync auth login --cui 12345678 --env prod
# A browser opens the ANAF authorize URL; present the cert (PIN).
# Your browser lands on the callback URL — copy the FULL address-bar URL
# (it contains ?code=...) and paste it back into the CLI prompt.
# Then copy the token to the server:
scp "$EFACTURA_SYNC_CONFIG_DIR/tokens/12345678.prod.json" \
    server:"$EFACTURA_SYNC_CONFIG_DIR/tokens/"
```
````

- [ ] **Step 3: Verify the doc reads correctly**

Run: `git diff README.md`
Expected: the `[not working...]` line is gone; the paste flow and `redirect_uri` config are documented; no stray references to a local callback server.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): document HTTPS redirect_uri + manual paste login"
```

---

## Final Verification

- [ ] **Run the full quality gate**

Run:
```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src
```
Expected: all green. If `ruff format --check` complains, run `uv run ruff format .` and amend the relevant commit.
