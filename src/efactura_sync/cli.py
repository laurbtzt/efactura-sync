"""Typer CLI."""

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import typer

from efactura_sync.anaf.oauth import (
    auth_code_login,
    load_token,
    refresh_access_token,
    save_token,
)
from efactura_sync.config import load_config
from efactura_sync.types import Env

app = typer.Typer(add_completion=False, no_args_is_help=True)
auth_app = typer.Typer(no_args_is_help=True, help="OAuth token management.")
app.add_typer(auth_app, name="auth")

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
_OPT_CUI_LOGIN = typer.Option(..., "--cui", help="CUI being authorized")
_OPT_CUI = typer.Option(..., "--cui")
_OPT_ENV = typer.Option("prod", "--env")


@app.callback()
def _main(
    ctx: typer.Context,
    config: Path = _OPT_CONFIG,
    secrets: Path = _OPT_SECRETS,
    tokens_dir: Path = _OPT_TOKENS_DIR,
) -> None:
    ctx.obj = {
        "config_path": config,
        "secrets_path": secrets,
        "tokens_dir": tokens_dir,
    }


def _validate_env(env: str) -> Env:
    if env not in ("prod", "test"):
        typer.echo(f"unknown env: {env!r}", err=True)
        raise typer.Exit(code=2)
    return cast(Env, env)


@auth_app.command("login")
def auth_login(
    ctx: typer.Context,
    cui: str = _OPT_CUI_LOGIN,
    env: str = _OPT_ENV,
) -> None:
    """Run the interactive OAuth2 authorization-code flow on a host with the cert."""
    env_typed = _validate_env(env)
    cfg = load_config(
        config_path=ctx.obj["config_path"],
        secrets_path=ctx.obj["secrets_path"],
    )
    client_id, client_secret = cfg.anaf_credentials(env_typed)
    now = datetime.now(UTC)
    with httpx.Client() as http:
        token = auth_code_login(
            http=http,
            env=env_typed,
            client_id=client_id,
            client_secret=client_secret,
            cui=cui,
            now=now,
        )
    save_token(ctx.obj["tokens_dir"], token)
    typer.echo(f"OK — token saved; expires {token.expires_at.isoformat()}")


@auth_app.command("refresh")
def auth_refresh(
    ctx: typer.Context,
    cui: str = _OPT_CUI,
    env: str = _OPT_ENV,
) -> None:
    """Refresh access token using the stored refresh token (no cert required)."""
    env_typed = _validate_env(env)
    cfg = load_config(
        config_path=ctx.obj["config_path"],
        secrets_path=ctx.obj["secrets_path"],
    )
    client_id, client_secret = cfg.anaf_credentials(env_typed)
    tok = load_token(ctx.obj["tokens_dir"], cui=cui, env=env)
    now = datetime.now(UTC)
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
    save_token(ctx.obj["tokens_dir"], new_tok)
    typer.echo(f"OK — refreshed; expires {new_tok.expires_at.isoformat()}")
