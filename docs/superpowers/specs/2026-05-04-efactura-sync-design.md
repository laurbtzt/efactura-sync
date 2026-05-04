# efactura-sync — Design

**Date:** 2026-05-04
**Status:** Design approved; awaiting implementation plan.

## 1. Problem statement

Pull invoices and related messages **from** Romanian ANAF e-Factura SPV into a local archive on a home server. Each monitored CUI has its own qualified digital certificate; we hold OAuth tokens per CUI. The system runs once a day, archives every message ANAF returns (received invoices, sent invoices, errors, notices), renders human-readable PDFs for invoices via ANAF's hosted `xmltopdf` endpoint, and emails the operator about specific events.

This is **read-only** with respect to ANAF — we never upload or modify anything. Storage is for archival, not accounting; the local DB exists only for deduplication, resume-from-failure state, and the per-CUI email allow-list.

### References

- `docs/Swagger links.md` — six ANAF Swagger endpoint URLs.
- `docs/Oauth_procedura_inregistrare_aplicatii_portal_ANAF.pdf` — ANAF OAuth app registration procedure.
- `docs/prezentare api efactura.pdf` — ANAF API overview.

## 2. Scope

### In scope
- Daily polling of ANAF SPV for 1–5 monitored CUIs against either the production or test environment (config-driven).
- Per-CUI OAuth2 lifecycle: interactive `auth login` on a laptop with cert plugged in; non-interactive `auth refresh` on the server; surface "90-day wall" approaching via `status`.
- Archival of all message types (`FACTURA PRIMITA`, `FACTURA TRIMISA`, `ERORI FACTURA`, other `MESAJ`) to a deterministic on-disk layout.
- PDF rendering for invoices (PRIMITA + TRIMISA) via ANAF's hosted `xmltopdf` endpoint.
- SQLite as the dedup ledger and resume-from-failure log.
- Email notifications per the rules in §6.
- Failure-notification email on uncaught run errors.
- Per-CUI allow-list of "tracked counterparties" (suppliers) that gates PRIMITA emails, managed via CLI.

### Out of scope (now)
- Submitting / generating outgoing invoices (no use of ANAF upload, state, validate endpoints).
- Sub-daily polling, parallelism across CUIs, real-time delivery.
- Parsing UBL into a normalised analytical schema; structured search.
- HTML email, web UI, mobile app.
- Healthchecks-style dead-man's switch (operator chose to skip it).
- A monitoring/observability stack beyond a rotated log file.

## 3. High-level architecture

A Python package `efactura_sync` (Python 3.12+, `uv` for tooling) that exposes one CLI run in two host roles:

- **Laptop, interactive, cert plugged in**
  - `auth login --cui --env` — one-time per (CUI, env) authorization-code flow against ANAF.
  - `auth refresh` — also runnable on laptop if needed.
  - Outputs a token JSON file the operator copies to the server.
- **Home server, headless, scheduled (cron, once daily)**
  - `sync run [--cui] [--env] [--dry-run]` — the daily job.
  - `auth refresh` — invoked automatically at the start of `sync run` when within 7 days of access-token expiry.

The server **never** pulls tokens from the laptop; the laptop **never** pushes anything except via a manual file copy initiated by the operator. The trust boundary is one-way and explicit.

### Daily run, per CUI

```
1. resume pass: scan synced_messages for rows with NULL markers; finish missing steps (download / render / email).
2. proactive token refresh if expires_at - now < 7 days.
3. GET /listaMesajeFactura?cif=<CUI>&zile=<N>   (N derived from poll_state, capped at 60).
4. for each message id not already in synced_messages:
     a. INSERT row with first_seen_at.
     b. GET /descarcare?id=<msg_id>             → write ZIP atomically; set zip_path.
     c. if PRIMITA or TRIMISA:
          extract UBL XML from ZIP.
          POST /transformare/<FACT1|FCN> XML    → write PDF atomically; set pdf_path.
     d. apply email rules; either send via SMTP and set email_sent_at,
        or set email_skip_reason.
5. UPDATE poll_state.last_polled_at.
```

A run completes per-CUI; one CUI's failure does not abort other CUIs.

## 4. Module layout

```
src/efactura_sync/
├── __init__.py
├── cli.py                 # argparse/typer wiring → calls into the rest
├── config.py              # load TOML, validate, env-aware (prod/test)
├── anaf/
│   ├── __init__.py
│   ├── oauth.py           # auth-code flow, token refresh, token store I/O
│   ├── client.py          # HTTP client wrapping the 3 endpoints we use
│   └── messages.py        # decode listamesaje JSON, ZIP extraction, UBL parsing
├── storage/
│   ├── __init__.py
│   ├── layout.py          # path computation
│   ├── files.py           # atomic write, .partial sweep
│   └── db.py              # SQLite schema, queries
├── mail.py                # SMTP sender, attachment builder, Romanian rendering
├── render.py              # POST XML to ANAF xmltopdf, return bytes
├── sync.py                # the orchestrator: per-CUI run, applies email rules
└── errors.py              # typed exceptions used across modules
```

Three external surfaces are abstracted behind interfaces so tests swap them:

| Surface | Real impl | Fake in tests |
|---|---|---|
| ANAF HTTP | `anaf.client.AnafClient` (httpx) | `FakeAnafClient` returning canned bytes/JSON |
| SMTP | `mail.Mailer` (`smtplib`) | `FakeMailer` collecting sent messages |
| Filesystem | `storage.files.FileStore` | `tmp_path` fixture |

The SQLite connection is also passed in (so tests use `:memory:`). No module-level globals for any of these.

### CLI subcommands

| Subcommand | Where it runs | Purpose |
|---|---|---|
| `auth login --cui --env` | laptop | Browser-based OAuth authorization-code flow. |
| `auth refresh [--cui] [--env]` | either | Refresh access token using stored refresh token. |
| `sync run [--cui] [--env] [--dry-run]` | server | The daily job. |
| `cui add <cui> [--name]` / `cui list` / `cui remove <cui>` | either | Register / inspect monitored CUIs. |
| `track add <counterparty_cui> --cui <my_cui>` / `track remove …` / `track list --cui …` | either | Manage per-CUI PRIMITA email allow-list. |
| `status` | either | Show last-poll, token expiry, pending/failed items, 90-day wall. |
| `replay <msg_id>` | either | Re-run download/render/email for one specific message (recovery). |

`--dry-run` walks the pipeline but skips writes, DB inserts, and emails — prints what *would* happen.

## 5. Data model

### 5.1 On-disk layout

```
$XDG_CONFIG_HOME/efactura-sync/                 # ~/.config/efactura-sync/
  config.toml                                   # non-secret app config
  secrets.toml             (chmod 0600)         # SMTP creds, ANAF client_id/secret
  tokens/
    <CUI>.<env>.json       (chmod 0600)         # OAuth tokens per (CUI, env)

$XDG_DATA_HOME/efactura-sync/                   # ~/.local/share/efactura-sync/
  state.db                                      # SQLite — single source of truth for state
  archive/                                      # archive root (override in config)
    <CUI>/
      <YYYY>/<MM>/                              # by invoice issue date (Bucharest local)
        received/
          archive/<msg_id>.zip
          pdf/<msg_id>.pdf
        sent/
          archive/<msg_id>.zip
          pdf/<msg_id>.pdf
      messages/<YYYY>/<MM>/<msg_id>.zip         # ERORI / MESAJ (no PDF)
  logs/efactura-sync.log                        # rotated daily
```

The archive root is overridable in `config.toml` (e.g., to point at a NAS mount).

The `<YYYY>/<MM>` partition uses the **invoice issue date** (Bucharest local), not the receipt date. For ERORI / MESAJ — which have no issue date — the partition uses ANAF's `data_creare` (Bucharest local).

### 5.2 `config.toml` (non-secrets, hand-edited)

```toml
[archive]
root = "~/.local/share/efactura-sync/archive"   # optional override

[smtp]
host = "smtp.fastmail.com"
port = 465
tls = "implicit"                                # "implicit" | "starttls"
from_addr = "efactura@yourdomain.tld"
to_addr = "you@yourdomain.tld"
error_to_addr = "you@yourdomain.tld"            # optional; defaults to to_addr

[anaf]
default_env = "prod"

[logging]
level = "INFO"
```

### 5.3 `secrets.toml` (chmod 0600, hand-edited, never logged)

```toml
[smtp]
username = "efactura@yourdomain.tld"
password = "app-password-here"

[anaf.prod]
client_id = "..."
client_secret = "..."

[anaf.test]
client_id = "..."
client_secret = "..."
```

### 5.4 `tokens/<CUI>.<env>.json` (one per CUI×env, chmod 0600)

```json
{
  "cui": "12345678",
  "env": "prod",
  "access_token": "...",
  "refresh_token": "...",
  "expires_at": "2026-08-01T10:00:00Z",
  "obtained_at": "2026-05-04T10:00:00Z"
}
```

### 5.5 SQLite schema (`state.db`)

```sql
CREATE TABLE monitored_cuis (
  cui            TEXT PRIMARY KEY,
  display_name   TEXT,
  added_at       TEXT NOT NULL                  -- ISO-8601 UTC
);

CREATE TABLE tracked_counterparties (
  my_cui            TEXT NOT NULL,
  counterparty_cui  TEXT NOT NULL,
  added_at          TEXT NOT NULL,
  PRIMARY KEY (my_cui, counterparty_cui),
  FOREIGN KEY (my_cui) REFERENCES monitored_cuis(cui) ON DELETE CASCADE
);

CREATE TABLE poll_state (
  cui              TEXT NOT NULL,
  env              TEXT NOT NULL,                -- "prod" | "test"
  last_polled_at   TEXT NOT NULL,                -- ISO-8601 UTC
  PRIMARY KEY (cui, env)
);

CREATE TABLE synced_messages (
  msg_id              TEXT NOT NULL,
  cui                 TEXT NOT NULL,
  env                 TEXT NOT NULL,
  msg_type            TEXT NOT NULL,             -- 'PRIMITA'|'TRIMISA'|'ERORI'|'MESAJ'
  counterparty_cui    TEXT,                      -- supplier (PRIMITA) / customer (TRIMISA); NULL otherwise
  issue_date          TEXT,                      -- YYYY-MM-DD (Bucharest local); NULL for non-invoices
  zip_path            TEXT,                      -- relative to archive root; set after download
  pdf_path            TEXT,                      -- set after render (NULL for ERORI/MESAJ)
  email_sent_at       TEXT,                      -- ISO-8601 UTC; NULL if not sent
  email_skip_reason   TEXT,                      -- 'filtered_by_track_list' | 'never_email_for_type' | NULL
  first_seen_at       TEXT NOT NULL,
  last_attempt_at     TEXT NOT NULL,
  last_error          TEXT,
  PRIMARY KEY (msg_id, cui, env)
);

CREATE INDEX idx_msg_cui ON synced_messages(cui, env);
CREATE INDEX idx_msg_pending ON synced_messages(cui, env)
  WHERE zip_path IS NULL
     OR (msg_type IN ('PRIMITA','TRIMISA') AND pdf_path IS NULL)
     OR (email_sent_at IS NULL AND email_skip_reason IS NULL);
```

`synced_messages` is both **dedup ledger** and **resume-from-failure log**: each step sets a column on success.

## 6. Email rules

| Type | Email? | Skip reason if not |
|---|---|---|
| `PRIMITA` | Only if supplier CUI ∈ `tracked_counterparties[my_cui]` | `filtered_by_track_list` |
| `TRIMISA` | Never | `never_email_for_type` |
| `ERORI` | Always | — |
| `MESAJ` | Always | — |

All four types are **always archived to disk** regardless of email rules.

`email_sent_at` is set in the same SQLite transaction as the SMTP success acknowledgement. Retries do not re-send a row whose `email_sent_at` is already populated.

### 6.1 Subject line (Romanian)

PRIMITA:
```
[factură] <numele_meu> · <nume_furnizor> (CUI <cui_furnizor>) · <număr_factură> · <data_emiterii>
```

ERORI / MESAJ:
```
[eroare|notificare] <numele_meu> · mesaj <msg_id> · <tip_ANAF>
```

`<numele_meu>` resolves from `monitored_cuis.display_name`, falling back to the CUI alone. `<nume_furnizor>` resolves from the UBL XML (`AccountingSupplierParty/Party/PartyLegalEntity/RegistrationName`), falling back to the supplier CUI. Missing fields are rendered as `—`.

### 6.2 Body (Romanian, plain text)

PRIMITA:
```
Sincronizare ANAF e-Factura — factură nouă primită

CUI propriu:    12345678 (Acme SRL)
Furnizor:       RO87654321 (Furnizor X SRL)
Număr factură:  INV-00451
Data emiterii:  2026-05-04
Total:          1234.56 RON
Stocat la:      archive/12345678/2026/05/received/
ID mesaj ANAF:  3001234567

Atașamente: arhiva ZIP semnată, PDF generat.
```

ERORI:
```
Sincronizare ANAF e-Factura — eroare primită de la ANAF

CUI propriu:   12345678 (Acme SRL)
ID mesaj ANAF: 3001234567
Tip mesaj:     ERORI FACTURA
Detalii ANAF:  <text din câmpul `detalii`>

Atașament: arhiva ZIP originală.
```

MESAJ:
```
Sincronizare ANAF e-Factura — notificare nouă

CUI propriu:   12345678 (Acme SRL)
ID mesaj ANAF: 3001234567
Tip mesaj:     <tip ANAF>
Detalii ANAF:  <text din câmpul `detalii`>

Atașament: arhiva ZIP originală.
```

### 6.3 Attachments

- PRIMITA: signed ZIP + rendered PDF.
- ERORI / MESAJ: signed ZIP only.

If a PDF render failed for a PRIMITA invoice but the ZIP downloaded successfully, the email **is still sent** with only the ZIP attached, and the body line becomes `PDF: în curs de generare — se va retrimite la următoarea rulare`. The PDF will be generated on retry, but no second email is sent (the row is already marked sent).

### 6.4 Delivery details

- One SMTP connection per run, reused across all messages.
- Per-message `Message-ID`: `<msg_id>.<env>@efactura-sync` — deterministic, so accidental retries are deduped client-side.
- Per-message SMTP failure → log + `last_error`, continue with next message.
- Total SMTP failure (auth/TLS broken) → abort the run loudly after the first failure (we don't want to silently sit on a backlog).

### 6.5 Failure-notification email

A top-level `try/except` in `cli.py` catches any uncaught exception during `sync run` and sends:

- **Subject:** `[eroare-rulare] efactura-sync — <date> — <hostname>`
- **Body (Romanian):** which CUI was being processed, which step failed (`listamesaje` / `descarcare` / `xmltopdf` / `email`), exception type, last 30 lines of the run log. Stack trace as plain-text attachment.
- **Recipient:** `[smtp].error_to_addr` if set, else `to_addr`.

Known gap: if SMTP itself is broken, this email won't go out. The error is still in `logs/efactura-sync.log` and `synced_messages.last_error`, surfaced by `efactura-sync status`.

Per-message non-fatal failures (e.g., one PDF render that timed out) do **not** trigger this email — they're captured in the row's `last_error` and retried on the next run.

## 7. ANAF integration details

### 7.1 OAuth flow (one-time per CUI×env)

ANAF uses authorization-code with a qualified digital certificate (PFX/eToken) presented during the browser redirect to ANAF's login page.

1. On the laptop (cert plugged in): `efactura-sync auth login --cui=<CUI> --env=<prod|test>`.
2. CLI starts a tiny local HTTP server on `127.0.0.1:<random_port>` and opens the system browser at the ANAF authorize URL (`oauth.anaf.ro` for prod, `logincert.anaf.ro` for test — config-driven), with `redirect_uri=http://127.0.0.1:<port>/callback`.
3. Browser hits ANAF; ANAF prompts for cert/token PIN; ANAF redirects to `127.0.0.1` with `?code=…`.
4. CLI exchanges the code at `…/token` for access + refresh tokens, writes `tokens/<cui>.<env>.json` (chmod 0600), prints `OK — token expires <date>`.
5. Operator copies the token file to the server (`scp` / rsync / USB).

`auth refresh` does only step 4 with the stored refresh token; **no cert required**, runs on the server.

**Proactive refresh:** at the start of every `sync run`, if `expires_at - now < 7 days`, refresh first.

**The 90-day wall:** ANAF refresh tokens are valid ~90 days from the original `auth login`, **not** from the last refresh. When hit, sync aborts that CUI cleanly with re-auth instructions. `efactura-sync status` surfaces "expires in N days" so the operator can plan.

### 7.2 Listing messages

```
GET /listaMesajeFactura?cif=<CUI>&zile=<N>
```

- `zile` is capped at **60** by ANAF.
- Daily run uses `zile = max(1, ceil((now - last_polled_at).days) + 1)`, clamped to 60. The `+1` is a safety overlap against clock skew.
- First run for a CUI uses `zile = 1` (forward-only, by operator choice — no backfill).
- Response: list of `{ id, cif, data_creare, tip, detalii }`.
- `tip` is one of `FACTURA PRIMITA`, `FACTURA TRIMISA`, `ERORI FACTURA`, plus other values lumped under `MESAJ`.
- Dedup via `INSERT ON CONFLICT DO NOTHING` keyed `(msg_id, cui, env)` — overlapping windows are safe.

### 7.3 Downloading

```
GET /descarcare?id=<msg_id>
```

Validation:
- Body must be a valid ZIP (`zipfile` opens it).
- For invoices: must contain at least one `.xml` member that parses as UBL Invoice.
- Atomic write: `<final>.partial` → `fsync` → `os.replace`.

### 7.4 XML → PDF

```
POST /transformare/<FACT1|FCN>   body = UBL XML, raw
```

We pick `FACT1` or `FCN` based on the invoice type derived from the message metadata or XML namespace. Timeout: 30s. Failure leaves `pdf_path` NULL with `last_error` populated; next run retries (download isn't repeated since `zip_path` is set).

### 7.5 Rate limits & resilience

- **Sequential** calls per CUI; no parallelism across CUIs in v1.
- Retry policy on transient failures: `[1s, 5s, 30s, 5m]` — four attempts, then leave the row pending for next run.
- Transient = network errors, HTTP 5xx, HTTP 429, SMTP `4xx`.
- Permanent = HTTP 4xx (auth or schema), SMTP `5xx` — not retried in this run; auth-related 4xx triggers the failure email.
- `User-Agent: efactura-sync/<version>` on all HTTP requests.

### 7.6 Time zones

All timestamps stored as **UTC** ISO-8601 in SQLite. Conversion to **Europe/Bucharest** local time happens at the boundary (computing `zile`, partitioning by issue date).

## 8. Error handling & idempotency

The five steps and their persistence markers in `synced_messages`:

| # | Step | Success marker |
|---|---|---|
| 1 | List → row inserted | `(msg_id, cui, env)` row exists |
| 2 | Download ZIP → atomic write | `zip_path` populated |
| 3 | Render PDF (PRIMITA + TRIMISA) | `pdf_path` populated |
| 4 | Apply email rules | `email_skip_reason` set, *or* step 5 reached |
| 5 | Send email + record | `email_sent_at` populated |

**Resume pass** runs at the start of every `sync run` *before* polling new messages: scan rows in this CUI/env where any expected column is NULL and finish those steps first.

### 8.1 Atomic file writes

1. Write to `<final>.partial` in the same directory.
2. `fsync(fd)`, `close`.
3. `os.replace(<final>.partial, <final>)`.

A startup sweep removes `<final>.partial` files older than 1h.

### 8.2 SQLite transactions

- Filesystem write happens **before** the DB column update. Crash leaves the file present and the column NULL → next run re-downloads (overwrite of identical bytes is fine), then sets the column.
- SMTP send happens **before** `email_sent_at` is set. Crash window between SMTP success and DB commit can produce one duplicate email; mitigated by the deterministic `Message-ID` so the recipient's mail client dedups.

### 8.3 Token refresh failures

- Success → continue.
- Refresh token expired (90-day wall) → abort that CUI cleanly, print laptop re-auth instructions, send failure email. Other CUIs continue.
- Network error → standard retry policy; if still failing, abort that CUI.

The 7-day buffer ensures a brief ANAF outage can't blackhole a run.

### 8.4 Safety valves

- `sync run --dry-run` walks the full pipeline but performs no writes, no DB inserts, no emails.
- `replay <msg_id>` re-runs steps 2–5 for one message, ignoring existing markers (recovery tool).

## 9. Testing

### 9.1 Unit tests (the bulk)

`pytest` with the abstracted seams replaced by fakes:

- **`anaf/oauth.py`** — token refresh logic, expiry math (7-day buffer, 90-day wall), error classification.
- **`anaf/messages.py`** — UBL XML parsing against fixtures at `tests/fixtures/ubl/`.
- **`storage/layout.py`** — pure path computation.
- **`storage/db.py`** — `:memory:` SQLite; covers dedup constraint, resume-pass query, email-skip transitions.
- **`mail.py`** — Romanian subject/body rendering against fixture data; `FakeMailer` captures messages.
- **`sync.py`** — the orchestrator. Most valuable layer. Scenarios:
  - Happy path: 3 PRIMITA + 2 TRIMISA + 1 ERORI → correct files, only filtered PRIMITA + ERORI emailed.
  - Resume after partial download: 2 rows missing `pdf_path` → run finishes only those two, emails them, no listamesaje call.
  - Track-list filter: PRIMITA from un-tracked supplier → archived, not emailed, `email_skip_reason='filtered_by_track_list'`.
  - PDF render fails for one of three PRIMITA → 3 emails (one with "PDF în curs"), retry succeeds, no second email.
  - Token refresh hits 90-day wall mid-run → CUI aborted, others continue, failure email sent.
  - SMTP total failure on first message → run aborts, no DB rows marked sent, retry on next run.

Coverage target: ≥ 90 % on `sync.py`, `oauth.py`, `db.py`. Lower elsewhere is fine.

### 9.2 Integration tests (handful, opt-in)

Marked `@pytest.mark.integration`, run via `uv run pytest -m integration`. One test runs `sync run --env=test` against the real ANAF test environment with a real test-CUI token (encrypted via `git-crypt` or skipped automatically if absent locally). Asserts the round-trip: list → download → xmltopdf → file on disk → email to a throwaway inbox. Run manually before releases; not in CI.

### 9.3 End-to-end smoke

`uv run efactura-sync sync run --env=prod --dry-run` is the cheap pre-commit sanity check — walks the pipeline against real ANAF prod, prints what would happen, writes nothing.

### 9.4 CI (GitHub Actions)

Matrix: `ubuntu-latest`, Python 3.12 + 3.13.

- `uv sync --dev`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy src/` (strict)
- `uv run pytest -m "not integration"`

No real ANAF or SMTP in CI. Everything offline.

### 9.5 Out of testing scope

- Cron / systemd timer wiring (configuration, not code).
- ANAF's `xmltopdf` output bytes — we trust ANAF; we test that we *call* it correctly.
- HTML email — we don't do HTML email.

## 10. Open / deferred

- **Healthchecks-style dead-man's switch:** rejected for v1; reconsider if "machine off / cron broken" goes undetected once.
- **Multi-CUI parallelism:** single-threaded for v1; per-CUI parallelism is a knob if scale grows.
- **Self-hosted ANAF `xmltopdf` fallback:** if ANAF rendering becomes unreliable, swap in a local XSLT + WeasyPrint pipeline behind the same `render.py` interface. No DB migration required.
- **Backfill mode:** forward-only by choice; a future `sync backfill --since=YYYY-MM-DD` would walk the 60-day window in chunks if needed.
