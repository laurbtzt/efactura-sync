# `auth login --no-browser`: paste-first login

**Date:** 2026-06-03
**Status:** Approved (pending spec review)

## Problem

`efactura-sync auth login` always calls `webbrowser.open(url)` before prompting
for the pasted redirect URL (`src/efactura_sync/cli.py`, `auth_login`). On a
headless server or over SSH there is no browser to open: `webbrowser.open` is
useless there and can emit noise or block. The flow already prints the URL and
prompts for the paste, so opening a browser is only a convenience for the laptop
case — it should be opt-in, not the default.

## Approach

Add a single Typer boolean option to `auth login`:

```
--browser / --no-browser   (default: --no-browser)
```

- **Default / `--no-browser`** — do not open a browser. Print the authorize URL
  minimally, then `typer.prompt` for the pasted redirect URL and exchange it —
  exactly today's paste step. This is the new default because the tool is often
  run remotely.
- **`--browser`** — additionally `webbrowser.open(url)` before prompting; the URL
  is still printed as a fallback.

This honors the requested `--no-browser` parameter, makes paste-first the
default (works everywhere), and keeps browser auto-open available for the laptop.

Rejected alternatives: a lone `--no-browser` flag that still opens by default
(contradicts the chosen "never open by default"); removing browser-opening
entirely (then the flag is meaningless and the laptop loses the convenience).

## Components

Only `src/efactura_sync/cli.py`, function `auth_login`. No change to
`src/efactura_sync/anaf/oauth.py` — `build_authorize_url` and `exchange_code`
already separate URL construction from the exchange, so the command just chooses
whether to call `webbrowser.open`.

### New option

Add a module-level option constant next to the others (e.g. after `_OPT_ENV`):

```python
_OPT_BROWSER = typer.Option(
    False,
    "--browser/--no-browser",
    help="Open the authorize URL in a browser (default: print it for manual paste).",
)
```

Add the parameter to `auth_login`:

```python
def auth_login(
    ctx: typer.Context,
    cui: str = _OPT_CUI_LOGIN,
    env: str = _OPT_ENV,
    browser: bool = _OPT_BROWSER,
) -> None:
```

### Behavior in `auth_login`

After `url, state = build_authorize_url(...)`:

- If `browser` is true: call `webbrowser.open(url)`; if it returns false, print a
  one-line "could not open automatically" note.
- Always print the URL block (minimal): a short instruction line, then the URL
  indented on its own line, so it is easy to copy whether or not a browser
  opened.
- Then `pasted = typer.prompt("Paste the redirect URL (or just the code)")` and
  `exchange_code(...)` as today.

Minimal output (both paths share the same URL block; the `--browser` path adds
at most the "could not open" note):

```
Open this URL in a browser with your ANAF certificate, then paste the
redirect URL (it contains ?code=...) below:

  <authorize url>
```

## Error handling

Unchanged. The missing-`redirect_uri` guard (exit code 2) and `exchange_code`
error handling (`AuthError` on bad state / missing code / non-200) are untouched.
`webbrowser.open` is only invoked on the `--browser` path.

## Testing

`tests/test_cli.py`, mirroring the existing `auth login` tests (monkeypatch
`build_authorize_url`/`exchange_code`, feed the paste via `input=`):

- **Default (no flag): browser is NOT opened.** Monkeypatch
  `efactura_sync.cli.webbrowser.open` to record calls; invoke `auth login`
  without flags; assert it was never called, the token is written, and
  `exchange_code` received the pasted value.
- **`--no-browser`: same as default** — browser not opened, token written.
- **`--browser`: browser IS opened.** Assert `webbrowser.open` was called with
  the built URL.
- Existing tests that currently rely on the browser-open default are updated to
  pass `--browser` where they assert opening, or to drop the assumption.

## Documentation

README "Onboard a CUI": note that `auth login` prints the URL for manual paste by
default (no browser needed — good for SSH), and that `--browser` opens it
automatically on a machine with a browser + certificate.

## Out of scope

- A fully non-interactive mode that prints the URL and exits without prompting
  (the paste step stays in the same invocation, as today).
- Auto-detecting headless environments (`$DISPLAY`/SSH) — the explicit flag is
  enough.
