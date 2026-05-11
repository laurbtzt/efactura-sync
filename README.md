# efactura-sync

Daily archival sync for Romanian ANAF e-Factura SPV. Read-only — pulls invoices and messages, never uploads. Files are archived locally; PDF rendering goes through ANAF's hosted `xmltopdf` service. Notifications go out as Romanian-language emails over SMTP.

See the design spec at [`docs/superpowers/specs/2026-05-04-efactura-sync-design.md`](docs/superpowers/specs/2026-05-04-efactura-sync-design.md) and the implementation plan at [`docs/superpowers/plans/2026-05-04-efactura-sync.md`](docs/superpowers/plans/2026-05-04-efactura-sync.md).

## Setup

Prerequisites: Python 3.14+, [`uv`](https://docs.astral.sh/uv/), and a registered ANAF OAuth application (see `docs/Oauth_procedura_inregistrare_aplicatii_portal_ANAF.pdf`).

```bash
uv sync --dev
```

Create config files:

```bash
mkdir -p ~/.config/efactura-sync/tokens
chmod 700 ~/.config/efactura-sync ~/.config/efactura-sync/tokens
```

`~/.config/efactura-sync/config.toml`:

```toml
[smtp]
host = "smtp.fastmail.com"
port = 465
tls = "implicit"
from_addr = "efactura@yourdomain.tld"
to_addr = "you@yourdomain.tld"

[anaf]
default_env = "prod"

[logging]
level = "INFO"
```

> The `[smtp]` section is validated at startup and used at runtime: per-message notifications go to `to_addr`, run-failure notifications to `error_to_addr` (defaults to `to_addr` if omitted).

`~/.config/efactura-sync/secrets.toml` (chmod 0600):

```toml
[smtp]
username = "efactura@yourdomain.tld"
password = "your-app-password"

[anaf.prod]
client_id = "from-anaf-portal"
client_secret = "from-anaf-portal"

[anaf.test]
client_id = "from-anaf-portal-test"
client_secret = "from-anaf-portal-test"
```

```bash
chmod 600 ~/.config/efactura-sync/secrets.toml
```

## Configuration paths

By default, `efactura-sync` follows the [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html):

| What | Default location |
|---|---|
| `config.toml`, `secrets.toml`, `tokens/` | `${XDG_CONFIG_HOME:-$HOME/.config}/efactura-sync/` |
| `state.db` | `${XDG_DATA_HOME:-$HOME/.local/share}/efactura-sync/` |

To put configuration somewhere else, set the relevant env var before running the CLI:

```bash
XDG_CONFIG_HOME="$HOME/.dotfiles/config" uv run efactura-sync status
```

Per the XDG spec, the env var must be an **absolute** path; empty or relative values are ignored and the `$HOME`-based fallback is used.

For one-off overrides, the following flags take precedence over the XDG defaults on every command:

- `--config <path>` — path to `config.toml`
- `--secrets <path>` — path to `secrets.toml`
- `--tokens-dir <path>` — directory holding per-CUI OAuth token files
- `--db <path>` — path to the SQLite state database

## Onboard a CUI

On the **laptop** (digital cert plugged in):

```bash
uv run efactura-sync cui add 12345678 --name "Acme SRL"
uv run efactura-sync auth login --cui 12345678 --env prod
# Browser opens, ANAF asks for cert PIN, finishes silently.
# Then copy the token to the server:
scp ~/.config/efactura-sync/tokens/12345678.prod.json server:~/.config/efactura-sync/tokens/
```

Add suppliers to the email allow-list (used by PRIMITA email gating once mail is wired):

```bash
uv run efactura-sync track add RO87654321 --cui 12345678
```

## Daily run (server)

```bash
uv run efactura-sync sync run --env prod
```

Schedule via cron once daily (example, 03:00 local):

```cron
0 3 * * * /usr/bin/env -S /home/youruser/.local/bin/uv run --project /home/youruser/efactura-sync efactura-sync sync run --env prod
```

What `sync run` does:
- Lists new ANAF messages per monitored CUI (clamped to 60 days, with a 1-day safety overlap).
- Downloads each new message's signed ZIP and writes it atomically into the archive tree.
- For PRIMITA / TRIMISA invoices, extracts the UBL XML, renders a PDF via ANAF's `xmltopdf` endpoint, and writes it next to the ZIP.
- Records every message in SQLite (`state.db`) with deduplication on `(msg_id, cui, env)`.
- Sends a Romanian-language email per spec rules: PRIMITA from a tracked supplier (ZIP + PDF), ERORI / MESAJ (ZIP). TRIMISA never emails (`email_skip_reason='never_email_for_type'`). PRIMITA from an untracked supplier is filtered (`'filtered_by_track_list'`). On send failure, `last_error` is set and the resume pass retries on the next run.
- Sends a single failure-notification email to `[smtp].error_to_addr` if any uncaught exception escapes the per-CUI loop.
- Advances `poll_state.last_polled_at` after both phases complete.

## Inspecting state

```bash
uv run efactura-sync status --env prod
uv run efactura-sync sync run --env prod --dry-run
uv run efactura-sync replay <msg_id> --cui 12345678 --env prod
```

`status` prints one line per monitored CUI: cui, display name, token expiry (with `(refresh due!)` flag if within 7 days), last poll timestamp, count of pending rows.

`replay` clears step markers on a single message so the next `sync run` re-processes it.

## Storage layout

```
~/.local/share/efactura-sync/
  state.db                          # SQLite — dedup ledger + resume log
  archive/<CUI>/<YYYY>/<MM>/
    received/archive/<msg_id>.zip   # PRIMITA — original signed ZIP
    received/pdf/<msg_id>.pdf       # PRIMITA — rendered PDF
    sent/archive/<msg_id>.zip       # TRIMISA
    sent/pdf/<msg_id>.pdf
  archive/<CUI>/messages/<YYYY>/<MM>/<msg_id>.zip   # ERORI / MESAJ
```

Path partitioning uses the **invoice issue date** (Bucharest local) for invoices and the ANAF `data_creare` for messages. Override the archive root via `[archive].root` in `config.toml`.

## Status

| Capability | v1 |
|---|---|
| Pull listamesaje + descarcare from ANAF (prod and test envs) | ✅ |
| Atomic ZIP write to disk + dedup ledger | ✅ |
| UBL XML extraction + invoice field parsing | ✅ |
| PDF rendering via ANAF's `xmltopdf` | ✅ |
| OAuth bootstrap (laptop with cert) + 90-day refresh | ✅ |
| Per-CUI run with resume of pending rows + poll | ✅ |
| CLI: `auth`, `cui`, `track`, `sync`, `status`, `replay` | ✅ |
| Email notification rendering (Romanian) | ✅ |
| SMTP `Mailer` (implicit TLS / STARTTLS) | ✅ |
| Per-message email send for new rows | ✅ |
| Failure-notification email on uncaught exceptions | ✅ |

Existing rows tagged `email_skip_reason='mail_pending_v1'` from earlier builds are not backfilled automatically — drain them via `efactura-sync replay <msg_id> --cui <cui> --env <env>` to re-process individually.

## Tests

```bash
uv run pytest                       # unit tests, offline
uv run pytest -m integration        # opt-in: hits real ANAF test env (none yet)
uv run ruff check . && uv run ruff format --check .
uv run mypy src
```

The CI workflow under `.github/workflows/ci.yml` runs all of the above on every push and PR. The `integration` marker is reserved for tests that require live credentials and is not run in CI.
