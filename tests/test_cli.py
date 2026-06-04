import sqlite3 as _sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from efactura_sync.anaf.oauth import Token, save_token
from efactura_sync.cli import app
from efactura_sync.storage.db import (
    init_schema as _init_schema,
)
from efactura_sync.storage.db import (
    list_monitored_cuis as _list_monitored_cuis,
)
from efactura_sync.storage.db import (
    list_watched_counterparties as _list_watched_counterparties,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def _env_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the CLI at tmp_path via env vars and write valid config + secrets.

    Autouse so every CLI test runs against a working config dir. Tests that need
    a missing-config or unset-env scenario override these via monkeypatch.
    """
    _write_fixture_files(tmp_path)
    monkeypatch.setenv("EFACTURA_SYNC_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("EFACTURA_SYNC_ARCHIVE_DIR", str(tmp_path / "archive"))


def _write_fixture_files(tmp_path: Path) -> tuple[Path, Path]:
    """Write minimal valid config.toml + secrets.toml; return their paths."""
    config_file = tmp_path / "config.toml"
    secrets_file = tmp_path / "secrets.toml"
    config_file.write_text(
        "[smtp]\nhost='h'\nport=465\ntls='implicit'\nfrom_addr='a'\nto_addr='b'\n"
        "[anaf]\ndefault_env='prod'\n"
        "[anaf.prod]\nredirect_uri='https://example.com/cb'\n"
        "[anaf.test]\nredirect_uri='https://example.com/cb'\n"
        "[logging]\nlevel='INFO'\n",
        encoding="utf-8",
    )
    secrets_file.write_text(
        "[smtp]\nusername='u'\npassword='p'\n"
        "[anaf.prod]\nclient_id='cid'\nclient_secret='cs'\n"
        "[anaf.test]\nclient_id='cid'\nclient_secret='cs'\n",
        encoding="utf-8",
    )
    return config_file, secrets_file


def test_top_level_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "auth" in result.stdout
    assert "cui" in result.stdout
    assert "watch" in result.stdout
    assert "sync" in result.stdout


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
    browser_calls: list[str] = []
    monkeypatch.setattr(
        "efactura_sync.cli.webbrowser.open",
        lambda url: browser_calls.append(url) or True,
    )

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
    assert browser_calls == []  # default does not open a browser


def test_invalid_env_exits_two(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["auth", "login", "--cui", "12345678", "--env", "staging"],
    )
    assert result.exit_code == 2
    # Error message goes to stderr; combine streams for robustness across Click versions.
    combined = (result.stdout or "") + (result.stderr or "")
    assert "staging" in combined or "unknown env" in combined


def test_auth_refresh_loads_and_writes_new_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from efactura_sync.anaf.oauth import Token
    from efactura_sync.anaf.oauth import save_token as real_save_token

    # Pre-write an existing token to disk.
    tokens_dir = tmp_path / "tokens"
    real_save_token(
        tokens_dir,
        Token(
            cui="12345678",
            env="prod",
            access_token="old-acc",
            refresh_token="old-ref",
            expires_at=datetime(2026, 8, 1, tzinfo=UTC),
            obtained_at=datetime(2026, 5, 4, tzinfo=UTC),
        ),
    )

    captured: dict[str, object] = {}

    def fake_refresh(**kwargs: object) -> Token:
        captured.update(kwargs)
        return Token(
            cui="12345678",
            env="prod",
            access_token="new-acc",
            refresh_token="new-ref",
            expires_at=datetime(2026, 11, 1, tzinfo=UTC),
            obtained_at=datetime(2026, 8, 1, tzinfo=UTC),
        )

    monkeypatch.setattr("efactura_sync.cli.refresh_access_token", fake_refresh)

    result = runner.invoke(
        app,
        ["auth", "refresh", "--cui", "12345678", "--env", "prod"],
    )
    assert result.exit_code == 0, result.stdout
    # The fake_refresh got called with the existing refresh_token.
    assert captured["refresh_token"] == "old-ref"
    # New token persisted with new access_token.
    from efactura_sync.anaf.oauth import load_token

    persisted = load_token(tokens_dir, cui="12345678", env="prod")
    assert persisted.access_token == "new-acc"
    assert persisted.refresh_token == "new-ref"


def test_auth_login_missing_config_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point at an empty config dir so config.toml/secrets.toml are absent.
    monkeypatch.setenv("EFACTURA_SYNC_CONFIG_DIR", str(tmp_path / "empty"))
    result = runner.invoke(
        app,
        ["auth", "login", "--cui", "12345678", "--env", "prod"],
    )
    assert result.exit_code != 0


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
    # No token file should have been written.
    assert not (tmp_path / "tokens" / "12345678.prod.json").exists()


def test_cui_add_list_remove(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"

    r1 = runner.invoke(app, ["cui", "add", "12345678", "--name", "Acme"])
    assert r1.exit_code == 0, r1.stdout

    r2 = runner.invoke(app, ["cui", "list"])
    assert r2.exit_code == 0, r2.stdout
    assert "12345678" in r2.stdout
    assert "Acme" in r2.stdout

    r3 = runner.invoke(app, ["cui", "remove", "12345678"])
    assert r3.exit_code == 0, r3.stdout

    # Verify by re-opening the DB directly: nothing left.
    conn = _sqlite3.connect(db_path)
    _init_schema(conn)
    try:
        assert _list_monitored_cuis(conn) == []
    finally:
        conn.close()


def test_cui_list_empty(tmp_path: Path) -> None:
    tmp_path / "state.db"
    result = runner.invoke(app, ["cui", "list"])
    assert result.exit_code == 0, result.stdout
    assert "no monitored CUIs" in result.stdout


def test_watch_add_list_remove(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    # Register the parent monitored CUI first.
    runner.invoke(app, ["cui", "add", "12345678"])

    r1 = runner.invoke(
        app,
        ["watch", "add", "RO111", "--cui", "12345678"],
    )
    assert r1.exit_code == 0, r1.stdout

    r2 = runner.invoke(app, ["watch", "list", "--cui", "12345678"])
    assert r2.exit_code == 0, r2.stdout
    assert "RO111" in r2.stdout

    r3 = runner.invoke(
        app,
        ["watch", "remove", "RO111", "--cui", "12345678"],
    )
    assert r3.exit_code == 0, r3.stdout

    conn = _sqlite3.connect(db_path)
    _init_schema(conn)
    try:
        assert _list_watched_counterparties(conn, my_cui="12345678") == []
    finally:
        conn.close()


def test_watch_add_without_parent_cui_fails(tmp_path: Path) -> None:
    """watch add requires the parent monitored CUI to exist first (FK constraint)."""
    tmp_path / "state.db"
    result = runner.invoke(
        app,
        ["watch", "add", "RO111", "--cui", "12345678"],  # 12345678 not added
    )
    assert result.exit_code != 0


def test_cui_remove_cascades_to_watched(tmp_path: Path) -> None:
    """`cui remove` should also remove rows in watched_counterparties (FK CASCADE)."""
    db_path = tmp_path / "state.db"
    runner.invoke(app, ["cui", "add", "12345678"])
    runner.invoke(
        app,
        ["watch", "add", "RO111", "--cui", "12345678"],
    )

    runner.invoke(app, ["cui", "remove", "12345678"])

    conn = _sqlite3.connect(db_path)
    _init_schema(conn)
    try:
        # Both monitored CUI and its watched counterparty are gone.
        assert _list_monitored_cuis(conn) == []
        assert _list_watched_counterparties(conn, my_cui="12345678") == []
    finally:
        conn.close()


def test_sync_run_invokes_run_for_cui_for_each_monitored_cui(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tmp_path / "state.db"
    # Pre-populate one monitored CUI.
    runner.invoke(app, ["cui", "add", "12345678"])
    # Pre-create a non-expired token file so needs_refresh is False.
    save_token(
        tmp_path / "tokens",
        Token(
            cui="12345678",
            env="prod",
            access_token="acc",
            refresh_token="ref",
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            obtained_at=datetime(2026, 5, 4, tzinfo=UTC),
        ),
    )

    calls: list[dict[str, object]] = []

    def fake_run(deps: object, **kwargs: object) -> object:
        calls.append(kwargs)
        from efactura_sync.sync import RunResult

        return RunResult(processed=0, failures=0)

    monkeypatch.setattr("efactura_sync.cli.run_for_cui", fake_run)

    result = runner.invoke(app, ["sync", "run", "--env", "prod", "--zile", "30"])
    assert result.exit_code == 0, result.stdout
    assert [c["my_cui"] for c in calls] == ["12345678"]
    assert calls[0]["zile_override"] == 30
    # Output mentions the cui and counts.
    assert "12345678" in result.stdout
    assert "processed=0" in result.stdout
    assert "failures=0" in result.stdout


def test_sync_run_dry_run_skips_real_work(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tmp_path / "state.db"
    runner.invoke(app, ["cui", "add", "12345678"])

    calls: list[object] = []

    def fake_run(deps: object, **kwargs: object) -> object:
        calls.append(kwargs)
        raise AssertionError("run_for_cui must not be called in --dry-run")

    monkeypatch.setattr("efactura_sync.cli.run_for_cui", fake_run)

    result = runner.invoke(
        app,
        ["sync", "run", "--env", "prod", "--dry-run"],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == []
    # Dry-run should mention the CUI it would have processed.
    assert "12345678" in result.stdout
    assert "[dry-run]" in result.stdout


def test_status_lists_cuis_and_token_state(tmp_path: Path) -> None:
    tmp_path / "state.db"
    runner.invoke(
        app,
        ["cui", "add", "12345678", "--name", "Acme"],
    )
    save_token(
        tmp_path / "tokens",
        Token(
            cui="12345678",
            env="prod",
            access_token="acc",
            refresh_token="ref",
            expires_at=datetime(2026, 8, 1, tzinfo=UTC),
            obtained_at=datetime(2026, 5, 4, tzinfo=UTC),
        ),
    )

    result = runner.invoke(app, ["status", "--env", "prod"])
    assert result.exit_code == 0, result.stdout
    assert "12345678" in result.stdout
    assert "Acme" in result.stdout
    assert "2026-08-01" in result.stdout  # expires_at


def test_replay_clears_step_markers(tmp_path: Path) -> None:
    """`replay <msg_id>` clears step markers so next sync re-runs that message."""
    from datetime import date

    db_path = tmp_path / "state.db"
    runner.invoke(app, ["cui", "add", "12345678"])

    # Pre-populate a synced_messages row that's fully done.
    from efactura_sync.storage.db import (
        SyncedMessage,
        connect,
        get_synced_message,
        init_schema,
        insert_synced_message,
        mark_email_sent,
        update_pdf_path,
        update_zip_path,
    )

    conn = connect(db_path)
    init_schema(conn)
    now = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
    insert_synced_message(
        conn,
        SyncedMessage(
            msg_id="3001",
            cui="12345678",
            env="prod",
            msg_type="PRIMITA",
            counterparty_cui="RO111",
            issue_date=date(2026, 5, 4),
            zip_path=None,
            pdf_path=None,
            email_sent_at=None,
            email_skip_reason=None,
            first_seen_at=now,
            last_attempt_at=now,
            last_error=None,
        ),
    )
    update_zip_path(conn, msg_id="3001", cui="12345678", env="prod", zip_path="z", now=now)
    update_pdf_path(conn, msg_id="3001", cui="12345678", env="prod", pdf_path="p", now=now)
    mark_email_sent(conn, msg_id="3001", cui="12345678", env="prod", sent_at=now)
    conn.close()

    result = runner.invoke(
        app,
        ["replay", "3001", "--cui", "12345678", "--env", "prod"],
    )
    assert result.exit_code == 0, result.stdout

    # Verify markers cleared.
    conn = connect(db_path)
    init_schema(conn)
    try:
        row = get_synced_message(conn, msg_id="3001", cui="12345678", env="prod")
    finally:
        conn.close()
    assert row is not None
    assert row.zip_path is None
    assert row.pdf_path is None
    assert row.email_sent_at is None
    assert row.email_skip_reason is None


def test_sync_run_refreshes_expiring_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If the stored token is within the refresh buffer, sync run refreshes it."""
    tmp_path / "state.db"
    runner.invoke(app, ["cui", "add", "12345678"])

    # Pre-save a token that's already past expiry — needs_refresh returns True.
    save_token(
        tmp_path / "tokens",
        Token(
            cui="12345678",
            env="prod",
            access_token="old-acc",
            refresh_token="old-ref",
            expires_at=datetime(2020, 1, 1, tzinfo=UTC),  # already expired
            obtained_at=datetime(2019, 1, 1, tzinfo=UTC),
        ),
    )

    refresh_calls: list[dict[str, object]] = []

    def fake_refresh(**kwargs: object) -> Token:
        refresh_calls.append(kwargs)
        return Token(
            cui=str(kwargs["cui"]),
            env="prod",
            access_token="new-acc",
            refresh_token="new-ref",
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            obtained_at=datetime(2026, 5, 4, tzinfo=UTC),
        )

    seen_tokens: list[str] = []

    def fake_run(deps: object, **kwargs: object) -> object:
        seen_tokens.append(str(kwargs["access_token"]))
        from efactura_sync.sync import RunResult

        return RunResult(processed=0, failures=0)

    monkeypatch.setattr("efactura_sync.cli.refresh_access_token", fake_refresh)
    monkeypatch.setattr("efactura_sync.cli.run_for_cui", fake_run)

    result = runner.invoke(app, ["sync", "run", "--env", "prod"])
    assert result.exit_code == 0, result.stdout

    # Refresh was called with the old refresh_token.
    assert len(refresh_calls) == 1
    assert refresh_calls[0]["refresh_token"] == "old-ref"
    # run_for_cui got the new access_token.
    assert seen_tokens == ["new-acc"]


def test_sync_run_filter_matches_one_of_many(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--cui X` runs only X, not the others."""
    tmp_path / "state.db"
    runner.invoke(app, ["cui", "add", "11111111"])
    runner.invoke(app, ["cui", "add", "22222222"])

    for cui in ("11111111", "22222222"):
        save_token(
            tmp_path / "tokens",
            Token(
                cui=cui,
                env="prod",
                access_token=f"acc-{cui}",
                refresh_token="ref",
                expires_at=datetime(2099, 1, 1, tzinfo=UTC),
                obtained_at=datetime(2026, 5, 4, tzinfo=UTC),
            ),
        )

    calls: list[str] = []

    def fake_run(deps: object, **kwargs: object) -> object:
        calls.append(str(kwargs["my_cui"]))
        from efactura_sync.sync import RunResult

        return RunResult(processed=0, failures=0)

    monkeypatch.setattr("efactura_sync.cli.run_for_cui", fake_run)

    result = runner.invoke(
        app,
        ["sync", "run", "--env", "prod", "--cui", "11111111"],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == ["11111111"]


def test_sync_run_filter_unregistered_cui_exits_two(tmp_path: Path) -> None:
    """`--cui Y` where Y isn't in monitored_cuis exits 2 with a clear message."""
    tmp_path / "state.db"
    runner.invoke(app, ["cui", "add", "11111111"])

    result = runner.invoke(
        app,
        ["sync", "run", "--env", "prod", "--cui", "99999999"],
    )
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "99999999" in combined
    assert "not monitored" in combined


def test_sync_run_sends_failure_email_on_uncaught_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tmp_path / "state.db"
    runner.invoke(app, ["cui", "add", "12345678"])

    save_token(
        tmp_path / "tokens",
        Token(
            cui="12345678",
            env="prod",
            access_token="acc",
            refresh_token="ref",
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            obtained_at=datetime(2026, 5, 4, tzinfo=UTC),
        ),
    )

    sent: list[tuple[object, str]] = []

    class _CapturingMailer:
        def __init__(self, **_: object) -> None:
            pass

        def send(self, msg: object, *, to_addr: str) -> None:
            sent.append((msg, to_addr))

    monkeypatch.setattr("efactura_sync.cli.Mailer", _CapturingMailer)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr("efactura_sync.cli.run_for_cui", boom)

    result = runner.invoke(app, ["sync", "run", "--env", "prod"])
    assert result.exit_code != 0
    assert sent, "expected a failure email"
    msg, to_addr = sent[0]
    assert "[eroare-rulare]" in msg.subject  # type: ignore[attr-defined]
    assert "kaboom" in msg.body or "RuntimeError" in msg.body  # type: ignore[attr-defined]


# --- XDG base directory helpers ------------------------------------------


def test_missing_config_dir_env_exits_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EFACTURA_SYNC_CONFIG_DIR", raising=False)
    result = runner.invoke(app, ["cui", "list"])
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "EFACTURA_SYNC_CONFIG_DIR is not set" in combined


def test_auth_login_no_browser_does_not_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from efactura_sync.anaf.oauth import Token

    browser_calls: list[str] = []
    monkeypatch.setattr(
        "efactura_sync.cli.build_authorize_url",
        lambda **_kw: ("https://authorize?x=1", "S1"),
    )
    monkeypatch.setattr(
        "efactura_sync.cli.webbrowser.open",
        lambda url: browser_calls.append(url) or True,
    )
    monkeypatch.setattr(
        "efactura_sync.cli.exchange_code",
        lambda **kwargs: Token(
            cui="12345678",
            env="prod",
            access_token="acc",
            refresh_token="ref",
            expires_at=datetime(2026, 8, 1, tzinfo=UTC),
            obtained_at=datetime(2026, 5, 4, tzinfo=UTC),
        ),
    )

    result = runner.invoke(
        app,
        ["auth", "login", "--cui", "12345678", "--env", "prod", "--no-browser"],
        input="https://example.com/cb?code=abc&state=S1\n",
    )
    assert result.exit_code == 0, result.stdout
    assert browser_calls == []
    assert "https://authorize?x=1" in result.stdout  # URL is printed for copying


def test_auth_login_browser_flag_opens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from efactura_sync.anaf.oauth import Token

    browser_calls: list[str] = []
    monkeypatch.setattr(
        "efactura_sync.cli.build_authorize_url",
        lambda **_kw: ("https://authorize?x=1", "S1"),
    )
    monkeypatch.setattr(
        "efactura_sync.cli.webbrowser.open",
        lambda url: browser_calls.append(url) or True,
    )
    monkeypatch.setattr(
        "efactura_sync.cli.exchange_code",
        lambda **kwargs: Token(
            cui="12345678",
            env="prod",
            access_token="acc",
            refresh_token="ref",
            expires_at=datetime(2026, 8, 1, tzinfo=UTC),
            obtained_at=datetime(2026, 5, 4, tzinfo=UTC),
        ),
    )

    result = runner.invoke(
        app,
        ["auth", "login", "--cui", "12345678", "--env", "prod", "--browser"],
        input="https://example.com/cb?code=abc&state=S1\n",
    )
    assert result.exit_code == 0, result.stdout
    assert browser_calls == ["https://authorize?x=1"]


def test_sync_run_without_zile_passes_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner.invoke(app, ["cui", "add", "12345678"])
    save_token(
        tmp_path / "tokens",
        Token(
            cui="12345678",
            env="prod",
            access_token="acc",
            refresh_token="ref",
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            obtained_at=datetime(2026, 5, 4, tzinfo=UTC),
        ),
    )
    calls: list[dict[str, object]] = []

    def fake_run(deps: object, **kwargs: object) -> object:
        calls.append(kwargs)
        from efactura_sync.sync import RunResult

        return RunResult(processed=0, failures=0)

    monkeypatch.setattr("efactura_sync.cli.run_for_cui", fake_run)
    result = runner.invoke(app, ["sync", "run", "--env", "prod"])
    assert result.exit_code == 0, result.stdout
    assert calls[0]["zile_override"] is None


def test_sync_run_zile_out_of_range_exits_two(tmp_path: Path) -> None:
    runner.invoke(app, ["cui", "add", "12345678"])
    too_big = runner.invoke(app, ["sync", "run", "--env", "prod", "--zile", "100"])
    assert too_big.exit_code == 2
    too_small = runner.invoke(app, ["sync", "run", "--env", "prod", "--zile", "0"])
    assert too_small.exit_code == 2


def test_sync_run_dry_run_shows_zile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner.invoke(app, ["cui", "add", "12345678"])
    result = runner.invoke(
        app,
        ["sync", "run", "--env", "prod", "--dry-run", "--zile", "60"],
    )
    assert result.exit_code == 0, result.stdout
    assert "[dry-run]" in result.stdout
    assert "zile=60" in result.stdout


def test_callback_configures_package_logger() -> None:
    import logging

    import efactura_sync.logging_setup as ls

    pkg = logging.getLogger("efactura_sync")
    for h in list(pkg.handlers):
        pkg.removeHandler(h)
    ls._configured = False

    result = runner.invoke(app, ["cui", "list"])
    assert result.exit_code == 0
    assert len(pkg.handlers) == 1
    assert pkg.propagate is True
