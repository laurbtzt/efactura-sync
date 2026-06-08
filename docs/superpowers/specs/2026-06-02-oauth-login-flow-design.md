# Fix the ANAF OAuth login flow

**Date:** 2026-06-02
**Status:** Approved (pending spec review)

## Problem

`auth_code_login` (`src/efactura_sync/anaf/oauth.py`) starts a local HTTP server
on `http://127.0.0.1:{random_port}/callback` and uses that as the OAuth
`redirect_uri`. ANAF requires the `redirect_uri` to **exactly match the Callback
URL registered in the ANAF OAuth profile**, and ANAF only accepts **HTTPS**
callback URLs — `http://localhost` / loopback registration is rejected. The
random port would never match a fixed registration either. The result: the
redirect never reaches the local server and login cannot complete. The README
currently carries a `[not working...]` note steering operators to Postman.

The OAuth procedure PDF (`docs/Oauth_procedura_inregistrare_aplicatii_portal_ANAF.pdf`)
documents the intended pattern: register a fixed HTTPS callback (Postman uses
`https://oauth.pstmn.io/v1/callback`) and capture the authorization `code` from
that redirect. The CLI cannot host a server at an external HTTPS URL.

The same PDF reveals two further bugs that would block login even after the
redirect is fixed:

1. **`token_content_type=jwt` is never sent.** It must be a **query** param on
   the authorize request and a **body** param on the token request. Without it,
   ANAF returns an opaque token instead of the 90-day JWT.
2. **Client credentials are sent in the form body.** The PDF requires HTTP
   **Basic Auth** (`Authorization: Basic base64(client_id:client_secret)`) on
   the token endpoint (both code-exchange and refresh).

## Approach: manual code paste with a configured HTTPS `redirect_uri` (Tier 0)

The redirect URL needs **no running server**. After cert auth, ANAF responds
with `302 Location: https://yourdomain/callback?code=...&state=...`; the browser
puts that URL in the **address bar** before attempting to load it, so the `code`
is captured even if the page 404s or the domain does not resolve. The only
requirement is that ANAF accepts the domain as one the operator controls at
registration time. The operator copies the redirect URL from the address bar and
pastes it into the CLI, which extracts the `code` and exchanges it.

The local-server design is removed entirely.

## Components

### `src/efactura_sync/anaf/oauth.py`

Replace `auth_code_login` (and delete `_CallbackHandler` + `HTTPServer` usage,
and the now-unused `webbrowser`/`http.server` imports) with two pure functions
the CLI orchestrates. Splitting I/O out of the network calls keeps both unit
testable without mocking stdin.

```python
def build_authorize_url(
    *, client_id: str, redirect_uri: str
) -> tuple[str, str]:
    """Return (authorize_url, state). state is a fresh CSRF token."""
```

The authorize URL carries: `response_type=code`, `client_id`, `redirect_uri`,
`state`, and `token_content_type=jwt`.

```python
def exchange_code(
    *,
    http: httpx.Client,
    env: Env,
    client_id: str,
    client_secret: str,
    cui: str,
    redirect_uri: str,
    redirect_response: str,   # full pasted URL OR a bare code
    expected_state: str,
    now: datetime,
) -> Token:
    ...
```

`exchange_code` behavior:

- Parse `redirect_response`. If it contains `?`/`code=`, treat it as a full URL
  and extract `code` + `state` from the query; otherwise treat the whole string
  (trimmed) as a bare `code` with no state.
- **Strict state check:** if a full URL was pasted, the returned `state` MUST
  equal `expected_state`, else raise `AuthError`. (A bare code carries no state
  to check — that path is for operators who copied only the code.)
- Missing/empty `code` → `AuthError`.
- POST to the token endpoint with:
  - **Header** `Authorization: Basic base64(client_id:client_secret)` (use
    `httpx`'s basic auth) — credentials no longer in the body.
  - Body: `grant_type=authorization_code`, `code`, `redirect_uri`,
    `token_content_type=jwt`.
- Build `Token` exactly as today (`expires_at = now + expires_in`).

### `refresh_access_token` (same file)

- Move `client_id`/`client_secret` from the body into a **Basic Auth header**.
- Keep `grant_type=refresh_token` + `refresh_token` in the body; add
  `token_content_type=jwt` to the body (same endpoint, keeps JWT on refresh).
- Error handling (`invalid_grant` → `RefreshTokenExpired`, etc.) unchanged.

### `src/efactura_sync/config.py`

- Add to `AnafConfig`: `prod_redirect_uri: str | None`,
  `test_redirect_uri: str | None`.
- Add method `anaf_redirect_uri(self, env: Env) -> str | None`.
- In `load_config`, read **optional** `redirect_uri` from `config.toml`
  `[anaf.prod]` / `[anaf.test]` (non-secret, lives in config.toml not
  secrets.toml). Absent → `None`. The `[anaf]` table keeps `default_env`;
  `[anaf.prod]`/`[anaf.test]` subtables are new and optional.

`redirect_uri` is **per-env** because prod and test are typically separate ANAF
OAuth apps with separate registrations.

### `src/efactura_sync/cli.py` — `auth login`

- Resolve `redirect_uri = config.anaf_redirect_uri(env)`. If `None`, exit with a
  clear error (code `2`) telling the operator to add `redirect_uri` under
  `[anaf.<env>]` in config.toml.
- `url, state = build_authorize_url(client_id=..., redirect_uri=...)`.
- Try `webbrowser.open(url)`; always print the URL as fallback plus
  instructions: complete cert auth, then paste the full redirect URL from the
  address bar (the page need not load).
- Read the pasted line with `typer.prompt(...)`.
- `token = exchange_code(..., redirect_response=pasted, expected_state=state, ...)`.
- `save_token(...)`; print success as today.

### README

Replace the "Onboard a CUI" login block:

- Remove the `[not working...]` line.
- Document: register the ANAF OAuth app with an HTTPS callback on a domain you
  control (no server required — Tier 0); add `redirect_uri` under
  `[anaf.prod]` / `[anaf.test]` in config.toml; run
  `efactura-sync auth login --cui <cui> --env <env>`; complete cert auth in the
  browser; paste the redirect URL back into the CLI; `scp` the token to the
  server as before.
- Add `redirect_uri` to the config.toml example.

## Error handling

- Missing `redirect_uri` for the chosen env → CLI exits `2` with guidance.
- State mismatch on a pasted full URL → `AuthError`.
- Missing/empty `code` → `AuthError`.
- Token-exchange non-200 → `AuthError` with status + body snippet (as today).

## Testing (TDD)

`tests/` unit tests, offline, mocking `httpx`:

- `build_authorize_url`: URL contains `response_type=code`, the configured
  `redirect_uri`, a non-empty `state`, and `token_content_type=jwt`; returned
  `state` matches the one embedded.
- `exchange_code`:
  - accepts a full pasted redirect URL and extracts the code;
  - accepts a bare code;
  - strict state mismatch on a full URL → `AuthError`;
  - missing code → `AuthError`;
  - request sends `Authorization: Basic ...` header and
    `token_content_type=jwt` + `grant_type=authorization_code` in the body;
  - builds `Token` with correct `expires_at`.
- `refresh_access_token`: sends Basic Auth header and `token_content_type=jwt`
  in the body; existing `invalid_grant` / non-200 paths still hold.
- `config`: `redirect_uri` parsed per-env; absent → `None`;
  `anaf_redirect_uri` returns the right value per env.

## Out of scope

- Tier 1 (static helper page) and Tier 2 (live auto-capture endpoint) — Tier 0
  needs no infra; a static page can be added later without code changes here.
- Token revocation endpoint.
