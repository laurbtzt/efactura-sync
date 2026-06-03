# Sync backfill window: 60-day first run + `--zile` override

**Date:** 2026-06-03
**Status:** Approved (pending spec review)

## Problem

The first `sync run` for a CUI fetches only 1 day of messages. `_zile_for_run`
(`src/efactura_sync/sync.py`) returns `1` when there is no `poll_state` row yet,
so onboarding a CUI and running the sync misses any invoices/messages older than
24 hours. There is no way to request a wider window for a one-off backfill.

ANAF's `listamesaje` accepts a `zile` (days) parameter capped at 60 (enforced
server-side and in `AnafClient.list_messages` via `max(1, min(60, zile))`).

## Approach

Two changes:

1. **First run backfills 60 days.** `_zile_for_run` returns `60` (instead of `1`)
   when `poll_state is None`. Onboarding then `sync run` pulls the last 60 days —
   ANAF's maximum. Subsequent runs are unchanged: `ceil(days since last poll) + 1`,
   clamped to `[1, 60]`.

2. **`--zile N` override on `sync run`.** Forces the lookback window for every CUI
   in that run, ignoring `poll_state`. `N` must be `1..60`; values outside that
   range are a hard error (Typer range validation), not a silent clamp.

Backfills are safe to re-run: dedup is on `(msg_id, cui, env)`, so re-scanning
overlapping days produces no duplicate rows. `last_polled_at` advances to `now`
after a run as today, so the pattern is "`--zile 60` once, then normal daily".

## Components

### `src/efactura_sync/sync.py`

`_zile_for_run` gains an `override` parameter and the new first-run default:

```python
def _zile_for_run(
    deps: SyncDeps, *, cui: str, env: Env, now: datetime, override: int | None = None
) -> int:
    if override is not None:
        return max(1, min(60, override))
    state = dbq.get_poll_state(deps.db, cui=cui, env=env)
    if state is None:
        return 60  # first run backfills ANAF's maximum window
    delta_days = math.ceil((now - state.last_polled_at).total_seconds() / 86400) + 1
    return max(1, min(60, delta_days))
```

The `max(1, min(60, override))` clamp is defense-in-depth for non-CLI callers;
the CLI already restricts input to `1..60` (below), so it is a no-op there.

`run_for_cui` gains `zile_override: int | None = None` and passes it through:

```python
def run_for_cui(
    deps: SyncDeps,
    *,
    my_cui: str,
    env: Env,
    access_token: str,
    now: datetime,
    zile_override: int | None = None,
) -> RunResult:
    ...
    zile = _zile_for_run(deps, cui=my_cui, env=env, now=now, override=zile_override)
    new_msgs = deps.anaf.list_messages(cif=my_cui, zile=zile, access_token=access_token)
```

No other part of `run_for_cui` changes.

### `src/efactura_sync/cli.py`

Add an option constant near the other `_OPT_*` definitions:

```python
_OPT_ZILE = typer.Option(
    None,
    "--zile",
    min=1,
    max=60,
    help="Override the lookback window in days (1-60). "
    "Default: 60 on first run, else days since last poll.",
)
```

Add `zile: int | None = _OPT_ZILE` to `sync_run_cmd`. Pass it to each
`run_for_cui(...)` call as `zile_override=zile`. In the `--dry-run` branch,
include the override when set, e.g.:

```python
if dry_run:
    for m in monitored:
        suffix = f" zile={zile}" if zile is not None else ""
        typer.echo(f"[dry-run] would sync cui={m.cui} env={env_typed}{suffix}")
    return
```

### `README.md`

In "Daily run" / "Inspecting state", note that the first `sync run` for a CUI
backfills up to 60 days, and document `--zile N` (1-60) for a one-off custom
window, e.g. `efactura-sync sync run --cui 12345678 --env prod --zile 60`.

## Error handling

- `--zile` outside `1..60` → Typer/Click range error, exit code 2. No clamp.
- `poll_state` still advances to `now` after a run (unchanged).
- Per-message error/resume paths unchanged.

## Testing

`tests/test_sync.py` (unit, against the in-memory DB):

- `_zile_for_run` returns `60` when there is no `poll_state` row (first run).
- `_zile_for_run` with `override=30` returns `30` and does not consult
  `poll_state`; `override=100` returns `60`, `override=0` returns `1` (clamp
  for non-CLI callers).
- existing run-path test: `run_for_cui(..., zile_override=45)` causes
  `list_messages` to be called with `zile=45` (assert via the fake ANAF client).
- any existing assertion that the first run uses `zile=1` is updated to `60`.

`tests/test_cli.py`:

- `sync run --zile 30` calls `run_for_cui` with `zile_override=30` (monkeypatch
  `run_for_cui`, capture kwargs).
- `sync run --zile 100` and `sync run --zile 0` exit with code 2.
- `sync run --dry-run --zile 60` prints the `zile=60` suffix.

## Out of scope

- A `--since YYYY-MM-DD` date form (chose raw `--zile`).
- A config.toml setting for the first-run window (first run is fixed at 60).
- Windows >60 days (ANAF does not support them).
