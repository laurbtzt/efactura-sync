"""Typer CLI."""

import sqlite3
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import typer

from efactura_sync.anaf.client import AnafClient
from efactura_sync.anaf.oauth import (
    auth_code_login,
    load_token,
    needs_refresh,
    refresh_access_token,
    save_token,
)
from efactura_sync.config import load_config
from efactura_sync.render import PdfRenderer
from efactura_sync.storage.db import (
    add_monitored_cui,
    add_tracked_counterparty,
    find_pending_rows,
    get_poll_state,
    init_schema,
    list_monitored_cuis,
    list_tracked_counterparties,
    remove_monitored_cui,
    remove_tracked_counterparty,
)
from efactura_sync.storage.db import connect as _db_connect
from efactura_sync.storage.files import FileStore
from efactura_sync.sync import SyncDeps, run_for_cui
from efactura_sync.types import Env

app = typer.Typer(add_completion=False, no_args_is_help=True)
auth_app = typer.Typer(no_args_is_help=True, help="OAuth token management.")
app.add_typer(auth_app, name="auth")
cui_app = typer.Typer(no_args_is_help=True, help="Manage monitored CUIs.")
track_app = typer.Typer(no_args_is_help=True, help="Manage PRIMITA email allow-list.")
sync_app = typer.Typer(no_args_is_help=True, help="Run the daily sync.")
app.add_typer(cui_app, name="cui")
app.add_typer(track_app, name="track")
app.add_typer(sync_app, name="sync")

_DEFAULT_CONFIG_DIR = Path.home() / ".config" / "efactura-sync"
_OPT_CONFIG = typer.Option(
    _DEFAULT_CONFIG_DIR / "config.toml",
    "--config",
    help="Path to config.toml",
)
_OPT_SECRETS = typer.Option(
    _DEFAULT_CONFIG_DIR / "secrets.toml",
    "--secrets",
    help="Path to secrets.toml",
)
_OPT_TOKENS_DIR = typer.Option(
    _DEFAULT_CONFIG_DIR / "tokens",
    "--tokens-dir",
    help="Directory holding per-CUI OAuth token files.",
)
_OPT_DB = typer.Option(
    Path.home() / ".local" / "share" / "efactura-sync" / "state.db",
    "--db",
    help="Path to SQLite state database.",
)
_OPT_CUI_LOGIN = typer.Option(..., "--cui", help="CUI being authorized")
_OPT_CUI = typer.Option(..., "--cui")
_OPT_ENV = typer.Option("prod", "--env")

_CUI_ARG = typer.Argument(..., help="Romanian fiscal identifier (CUI).")
_NAME_OPT = typer.Option(None, "--name", help="Display name (used in email subjects later).")
_TRACK_CUI_OPT = typer.Option(
    ..., "--cui", help="The monitored CUI for which we track counterparties."
)
_COUNTERPARTY_ARG = typer.Argument(
    ..., help="Counterparty (supplier) CUI to track for PRIMITA email."
)
_OPT_CUI_FILTER = typer.Option(
    None, "--cui", help="Only run this CUI; default = all monitored CUIs."
)
_OPT_DRY_RUN = typer.Option(
    False, "--dry-run", help="Walk the pipeline but do not write or call ANAF."
)
_REPLAY_MSG_ID_ARG = typer.Argument(..., help="ANAF message id to replay.")


@dataclass(frozen=True)
class _CliContext:
    config_path: Path
    secrets_path: Path
    tokens_dir: Path
    db_path: Path


@app.callback()
def _main(
    ctx: typer.Context,
    config: Path = _OPT_CONFIG,
    secrets: Path = _OPT_SECRETS,
    tokens_dir: Path = _OPT_TOKENS_DIR,
    db_path: Path = _OPT_DB,
) -> None:
    ctx.obj = _CliContext(
        config_path=config,
        secrets_path=secrets,
        tokens_dir=tokens_dir,
        db_path=db_path,
    )


def _validate_env(env: str) -> Env:
    if env not in ("prod", "test"):
        typer.echo(f"unknown env: {env!r}", err=True)
        raise typer.Exit(code=2)
    return cast(Env, env)


def _ctx(ctx: typer.Context) -> _CliContext:
    obj = ctx.obj
    assert isinstance(obj, _CliContext)
    return obj


def _open_db(ctx: typer.Context) -> sqlite3.Connection:
    """Open the state DB, ensuring schema + foreign keys are on."""
    cli_ctx = _ctx(ctx)
    cli_ctx.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _db_connect(cli_ctx.db_path)
    init_schema(conn)
    return conn


def _prepare(ctx: typer.Context, env: str) -> tuple[_CliContext, Env, str, str, datetime]:
    """Run the common preamble for any command that needs config + ANAF creds.

    Returns (cli_ctx, env_typed, client_id, client_secret, now_utc).
    """
    cli_ctx = _ctx(ctx)
    env_typed = _validate_env(env)
    cfg = load_config(
        config_path=cli_ctx.config_path,
        secrets_path=cli_ctx.secrets_path,
    )
    client_id, client_secret = cfg.anaf_credentials(env_typed)
    now = datetime.now(UTC)
    return cli_ctx, env_typed, client_id, client_secret, now


@auth_app.command("login")
def auth_login(
    ctx: typer.Context,
    cui: str = _OPT_CUI_LOGIN,
    env: str = _OPT_ENV,
) -> None:
    """Run the interactive OAuth2 authorization-code flow on a host with the cert."""
    cli_ctx, env_typed, client_id, client_secret, now = _prepare(ctx, env)
    with httpx.Client() as http:
        token = auth_code_login(
            http=http,
            env=env_typed,
            client_id=client_id,
            client_secret=client_secret,
            cui=cui,
            now=now,
        )
    save_token(cli_ctx.tokens_dir, token)
    typer.echo(f"OK — token saved; expires {token.expires_at.isoformat()}")


@auth_app.command("refresh")
def auth_refresh(
    ctx: typer.Context,
    cui: str = _OPT_CUI,
    env: str = _OPT_ENV,
) -> None:
    """Refresh access token using the stored refresh token (no cert required)."""
    cli_ctx, env_typed, client_id, client_secret, now = _prepare(ctx, env)
    tok = load_token(cli_ctx.tokens_dir, cui=cui, env=env)
    with httpx.Client() as http:
        new_tok = refresh_access_token(
            http=http,
            env=env_typed,
            client_id=client_id,
            client_secret=client_secret,
            cui=cui,
            refresh_token=tok.refresh_token,
            now=now,
        )
    save_token(cli_ctx.tokens_dir, new_tok)
    typer.echo(f"OK — refreshed; expires {new_tok.expires_at.isoformat()}")


# --- cui subcommands ---


@cui_app.command("add")
def cui_add_cmd(
    ctx: typer.Context,
    cui: str = _CUI_ARG,
    name: str | None = _NAME_OPT,
) -> None:
    """Register a new monitored CUI (idempotent UPSERT — re-adding updates the display name)."""
    conn = _open_db(ctx)
    try:
        add_monitored_cui(conn, cui=cui, display_name=name, now=datetime.now(UTC))
    finally:
        conn.close()
    typer.echo(f"OK — monitored cui {cui}")


@cui_app.command("list")
def cui_list_cmd(ctx: typer.Context) -> None:
    """List all monitored CUIs."""
    conn = _open_db(ctx)
    try:
        rows = list_monitored_cuis(conn)
    finally:
        conn.close()
    if not rows:
        typer.echo("(no monitored CUIs yet — use 'cui add')")
        return
    for row in rows:
        display = row.display_name or "—"
        typer.echo(f"{row.cui}\t{display}")


@cui_app.command("remove")
def cui_remove_cmd(ctx: typer.Context, cui: str = _CUI_ARG) -> None:
    """Remove a monitored CUI and all its rows (poll_state, synced_messages, tracked)."""
    conn = _open_db(ctx)
    try:
        remove_monitored_cui(conn, cui=cui)
    finally:
        conn.close()
    typer.echo(f"OK — removed cui {cui}")


# --- track subcommands ---


@track_app.command("add")
def track_add_cmd(
    ctx: typer.Context,
    counterparty_cui: str = _COUNTERPARTY_ARG,
    cui: str = _TRACK_CUI_OPT,
) -> None:
    """Add a counterparty to the PRIMITA email allow-list for one monitored CUI."""
    conn = _open_db(ctx)
    try:
        add_tracked_counterparty(
            conn,
            my_cui=cui,
            counterparty_cui=counterparty_cui,
            now=datetime.now(UTC),
        )
    finally:
        conn.close()
    typer.echo(f"OK — tracking {counterparty_cui} for cui {cui}")


@track_app.command("list")
def track_list_cmd(ctx: typer.Context, cui: str = _TRACK_CUI_OPT) -> None:
    """List tracked counterparties for one monitored CUI."""
    conn = _open_db(ctx)
    try:
        rows = list_tracked_counterparties(conn, my_cui=cui)
    finally:
        conn.close()
    if not rows:
        typer.echo(f"(no tracked counterparties for cui {cui})")
        return
    for c in rows:
        typer.echo(c)


@track_app.command("remove")
def track_remove_cmd(
    ctx: typer.Context,
    counterparty_cui: str = _COUNTERPARTY_ARG,
    cui: str = _TRACK_CUI_OPT,
) -> None:
    """Remove a counterparty from the PRIMITA email allow-list."""
    conn = _open_db(ctx)
    try:
        remove_tracked_counterparty(conn, my_cui=cui, counterparty_cui=counterparty_cui)
    finally:
        conn.close()
    typer.echo(f"OK — untracked {counterparty_cui} from cui {cui}")


# --- sync / status / replay ---


@sync_app.command("run")
def sync_run_cmd(
    ctx: typer.Context,
    cui: str | None = _OPT_CUI_FILTER,
    env: str = _OPT_ENV,
    dry_run: bool = _OPT_DRY_RUN,
) -> None:
    """Run the daily sync for one or all monitored CUIs."""
    cli_ctx, env_typed, client_id, client_secret, now = _prepare(ctx, env)

    conn = _open_db(ctx)
    try:
        monitored = list_monitored_cuis(conn)
    finally:
        conn.close()

    if cui is not None:
        monitored = [m for m in monitored if m.cui == cui]
    if not monitored:
        typer.echo("no monitored CUIs to sync")
        return

    if dry_run:
        for m in monitored:
            typer.echo(f"[dry-run] would sync cui={m.cui} env={env_typed}")
        return

    cfg = load_config(
        config_path=cli_ctx.config_path,
        secrets_path=cli_ctx.secrets_path,
    )
    archive_root = cfg.archive_root

    try:
        with httpx.Client() as http:
            anaf = AnafClient(http=http, env=env_typed)
            renderer = PdfRenderer(http=http, env=env_typed)
            for m in monitored:
                conn = _open_db(ctx)
                try:
                    tok = load_token(cli_ctx.tokens_dir, cui=m.cui, env=env)
                    if needs_refresh(tok, now=now):
                        tok = refresh_access_token(
                            http=http,
                            env=env_typed,
                            client_id=client_id,
                            client_secret=client_secret,
                            cui=m.cui,
                            refresh_token=tok.refresh_token,
                            now=now,
                        )
                        save_token(cli_ctx.tokens_dir, tok)
                    deps = SyncDeps(
                        anaf=anaf,
                        renderer=renderer,
                        files=FileStore(),
                        db=conn,
                        archive_root=archive_root,
                    )
                    result = run_for_cui(
                        deps,
                        my_cui=m.cui,
                        env=env_typed,
                        access_token=tok.access_token,
                        now=now,
                    )
                    typer.echo(
                        f"cui={m.cui} env={env_typed} "
                        f"processed={result.processed} failures={result.failures}"
                    )
                finally:
                    conn.close()
    except Exception:
        # NOTE(mail-deferred): when mail.py lands, wrap this in a Mailer.send
        # of a render_failure_email() per spec §6.5. For now, surface the
        # traceback to stderr and exit non-zero so cron alerts.
        typer.echo("sync run failed:", err=True)
        traceback.print_exc()
        raise typer.Exit(code=1) from None


@app.command("status")
def status_cmd(
    ctx: typer.Context,
    env: str = _OPT_ENV,
) -> None:
    """Show monitored CUIs, token expiry, last poll, pending counts."""
    env_typed = _validate_env(env)
    cli_ctx = _ctx(ctx)
    now = datetime.now(UTC)

    conn = _open_db(ctx)
    try:
        cuis = list_monitored_cuis(conn)
        if not cuis:
            typer.echo("(no monitored CUIs yet — use 'cui add')")
            return
        for c in cuis:
            try:
                tok = load_token(cli_ctx.tokens_dir, cui=c.cui, env=env)
                tok_str = f"expires {tok.expires_at.isoformat()}"
                if needs_refresh(tok, now=now):
                    tok_str += " (refresh due!)"
            except Exception:
                tok_str = "no token"

            state = get_poll_state(conn, cui=c.cui, env=env_typed)
            last_poll = state.last_polled_at.isoformat() if state else "never"
            pending = len(find_pending_rows(conn, cui=c.cui, env=env_typed))
            display = c.display_name or "—"
            typer.echo(
                f"{c.cui}\t{display}\ttoken: {tok_str}\tlast poll: {last_poll}\tpending: {pending}"
            )
    finally:
        conn.close()


@app.command("replay")
def replay_cmd(
    ctx: typer.Context,
    msg_id: str = _REPLAY_MSG_ID_ARG,
    cui: str = _TRACK_CUI_OPT,
    env: str = _OPT_ENV,
) -> None:
    """Clear step markers on one message so the next sync run re-processes it.

    This does NOT trigger the run itself — invoke ``sync run`` afterwards.
    """
    env_typed = _validate_env(env)
    conn = _open_db(ctx)
    try:
        conn.execute(
            "UPDATE synced_messages SET "
            "zip_path=NULL, pdf_path=NULL, email_sent_at=NULL, email_skip_reason=NULL "
            "WHERE msg_id=? AND cui=? AND env=?",
            (msg_id, cui, env_typed),
        )
        conn.commit()
    finally:
        conn.close()
    typer.echo(
        f"replay queued for msg={msg_id} cui={cui}; "
        f"run 'sync run --cui={cui} --env={env}' to process"
    )
