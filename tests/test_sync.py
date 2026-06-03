import io
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

from efactura_sync.anaf.messages import ListMessage
from efactura_sync.errors import RenderError
from efactura_sync.mail import EmailMessage
from efactura_sync.storage.db import (
    add_monitored_cui,
    add_watched_counterparty,
    get_synced_message,
    init_schema,
    upsert_poll_state,
)
from efactura_sync.storage.files import FileStore
from efactura_sync.sync import RunResult, SyncDeps, process_one_message, run_for_cui

# --- fakes ----------------------------------------------------------------


class RecordingMailer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[EmailMessage, str]] = []

    def send(self, msg: EmailMessage, *, to_addr: str) -> None:
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append((msg, to_addr))


class FakeAnaf:
    def __init__(
        self,
        *,
        list_response: list[ListMessage] | None = None,
        download_payload: bytes | None = None,
    ) -> None:
        self.list_response = list_response or []
        self.download_payload = download_payload or b""
        self.list_calls: list[tuple[str, int]] = []
        self.download_calls: list[str] = []

    def list_messages(self, *, cif: str, zile: int, access_token: str) -> list[ListMessage]:
        self.list_calls.append((cif, zile))
        return self.list_response

    def download(self, *, msg_id: str, access_token: str) -> bytes:
        self.download_calls.append(msg_id)
        return self.download_payload


class FakeRenderer:
    def __init__(self, *, fail: bool = False, output: bytes = b"%PDF-fake") -> None:
        self.fail = fail
        self.output = output
        self.calls: list[bytes] = []

    def render(self, *, ubl_xml: bytes, standard: str = "FACT1") -> bytes:
        self.calls.append(ubl_xml)
        if self.fail:
            raise RenderError("boom")
        return self.output


# --- fixtures -------------------------------------------------------------


@pytest.fixture
def deps(db: sqlite3.Connection, archive_root: Path) -> SyncDeps:
    init_schema(db)
    return SyncDeps(
        anaf=FakeAnaf(),
        renderer=FakeRenderer(),
        files=FileStore(),
        db=db,
        archive_root=archive_root,
        mailer=RecordingMailer(),
        to_addr="me@example.com",
    )


# --- helpers --------------------------------------------------------------


UBL_FIXTURE = (Path(__file__).parent / "fixtures" / "ubl" / "primita_minimal.xml").read_bytes()


def _make_zip(xml_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("invoice.xml", xml_bytes)
    return buf.getvalue()


def _list_msg(**overrides: Any) -> ListMessage:
    base = ListMessage(
        msg_id="3001",
        cif="12345678",
        data_creare_utc=datetime(2026, 5, 4, 8, 30, tzinfo=UTC),
        tip_raw="FACTURA PRIMITA",
        tip="PRIMITA",
        detalii="ok",
    )
    return ListMessage(**{**base.__dict__, **overrides})


# --- tests ----------------------------------------------------------------


def test_primita_watched_supplier_archives_and_marks_pending(
    deps: SyncDeps, now_utc: datetime
) -> None:
    deps.anaf.download_payload = _make_zip(UBL_FIXTURE)  # type: ignore[attr-defined]
    add_monitored_cui(deps.db, cui="12345678", display_name="Acme", now=now_utc)
    add_watched_counterparty(deps.db, my_cui="12345678", counterparty_cui="RO87654321", now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        my_display_name="Acme",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.zip_path is not None
    assert row.pdf_path is not None
    assert row.email_skip_reason is None
    assert row.email_sent_at is not None
    sent = deps.mailer.sent  # type: ignore[attr-defined]
    assert len(sent) == 1
    email, to_addr = sent[0]
    assert to_addr == "me@example.com"
    assert email.subject.startswith("[factură]")
    assert "Acme" in email.subject


def test_primita_unwatched_supplier_archives_but_filters_email(
    deps: SyncDeps, now_utc: datetime
) -> None:
    deps.anaf.download_payload = _make_zip(UBL_FIXTURE)  # type: ignore[attr-defined]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    # NOTE: no watched counterparty added.

    process_one_message(
        deps,
        my_cui="12345678",
        my_display_name="Acme",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.email_skip_reason == "filtered_by_watchlist"
    assert row.email_sent_at is None


def test_trimisa_archives_renders_pdf_and_marks_never_email(
    deps: SyncDeps, now_utc: datetime
) -> None:
    deps.anaf.download_payload = _make_zip(UBL_FIXTURE)  # type: ignore[attr-defined]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        my_display_name="Acme",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(tip_raw="FACTURA TRIMISA", tip="TRIMISA"),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.zip_path is not None
    assert row.pdf_path is not None
    assert row.email_skip_reason == "never_email_for_type"


def test_erori_archives_to_messages_and_marks_pending(deps: SyncDeps, now_utc: datetime) -> None:
    deps.anaf.download_payload = b"PKfake"  # type: ignore[attr-defined]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        my_display_name="Acme",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(tip_raw="ERORI FACTURA", tip="ERORI"),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.zip_path is not None
    assert "messages/" in row.zip_path
    assert row.pdf_path is None
    assert row.email_skip_reason is None
    assert row.email_sent_at is not None
    [(email, _)] = deps.mailer.sent  # type: ignore[attr-defined]
    assert email.subject.startswith("[eroare]")


def test_mesaj_archives_to_messages_and_marks_pending(deps: SyncDeps, now_utc: datetime) -> None:
    deps.anaf.download_payload = b"PKfake"  # type: ignore[attr-defined]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        my_display_name="Acme",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(tip_raw="MESAJ_CUMPARATOR", tip="MESAJ"),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.zip_path is not None
    assert row.pdf_path is None
    assert row.email_skip_reason is None
    assert row.email_sent_at is not None
    [(email, _)] = deps.mailer.sent  # type: ignore[attr-defined]
    assert email.subject.startswith("[notificare]")


def test_pdf_render_failure_records_error_and_marks_pending(
    deps: SyncDeps, now_utc: datetime
) -> None:
    deps.anaf.download_payload = _make_zip(UBL_FIXTURE)  # type: ignore[attr-defined]
    deps.renderer = FakeRenderer(fail=True)  # type: ignore[assignment]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    add_watched_counterparty(deps.db, my_cui="12345678", counterparty_cui="RO87654321", now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        my_display_name="Acme",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.zip_path is not None
    assert row.pdf_path is None
    assert row.last_error is not None and "boom" in row.last_error
    # Email decision was deferred because the row is still pending render. We do
    # NOT mark email_skip_reason here — the resume pass will retry render, and
    # after a successful render the email step will mark the row pending.
    assert row.email_skip_reason is None
    assert row.email_sent_at is None


def test_process_one_message_is_idempotent_on_reentry(deps: SyncDeps, now_utc: datetime) -> None:
    """Calling twice with the same list_msg downloads + emails-decides only once."""
    deps.anaf.download_payload = _make_zip(UBL_FIXTURE)  # type: ignore[attr-defined]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    add_watched_counterparty(deps.db, my_cui="12345678", counterparty_cui="RO87654321", now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        my_display_name="Acme",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(),
        now=now_utc,
    )
    # Second invocation must not re-download or change email decision.
    process_one_message(
        deps,
        my_cui="12345678",
        my_display_name="Acme",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(),
        now=now_utc,
    )

    assert deps.anaf.download_calls == ["3001"]  # type: ignore[attr-defined]
    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.email_sent_at is not None
    # Idempotent: second invocation must NOT re-send.
    assert len(deps.mailer.sent) == 1  # type: ignore[attr-defined]


def test_process_one_message_parse_failure_records_error(deps: SyncDeps, now_utc: datetime) -> None:
    """A ZIP that contains no UBL Invoice yields InvalidArchiveError -> last_error set."""
    # ZIP with only a non-UBL file inside.
    bad_zip = _make_zip(b"<not-an-invoice/>")
    deps.anaf.download_payload = bad_zip  # type: ignore[attr-defined]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        my_display_name="Acme",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.zip_path is None  # not written because parse failed
    assert row.last_error is not None and "parse:" in row.last_error
    assert row.email_skip_reason is None  # not decided yet


def test_process_one_message_download_failure_records_error(
    deps: SyncDeps, now_utc: datetime
) -> None:
    """A download error sets last_error and leaves the row pending."""
    from efactura_sync.errors import TransientError

    class FailingAnaf(FakeAnaf):
        def download(self, *, msg_id: str, access_token: str) -> bytes:
            self.download_calls.append(msg_id)
            raise TransientError("network blip", status=503, body=b"")

    deps.anaf = FailingAnaf()  # type: ignore[assignment]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        my_display_name="Acme",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.zip_path is None
    assert row.last_error is not None and "download:" in row.last_error
    assert row.email_skip_reason is None


def test_primita_with_null_counterparty_filters_email_as_unwatched(
    deps: SyncDeps, now_utc: datetime
) -> None:
    """A PRIMITA whose UBL has no supplier CUI can't be allow-list-checked, so skip."""
    # Build a UBL fixture with the supplier CompanyID stripped out.
    no_supplier_xml = UBL_FIXTURE.replace(b"<cbc:CompanyID>RO87654321</cbc:CompanyID>", b"")
    deps.anaf.download_payload = _make_zip(no_supplier_xml)  # type: ignore[attr-defined]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        my_display_name="Acme",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.counterparty_cui is None
    assert row.email_skip_reason == "filtered_by_watchlist"
    assert row.email_sent_at is None
    assert deps.mailer.sent == []  # type: ignore[attr-defined]


def test_run_for_cui_polls_and_processes(deps: SyncDeps, now_utc: datetime) -> None:
    deps.anaf = FakeAnaf(  # type: ignore[assignment]
        list_response=[_list_msg()],
        download_payload=_make_zip(UBL_FIXTURE),
    )
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    add_watched_counterparty(deps.db, my_cui="12345678", counterparty_cui="RO87654321", now=now_utc)

    result = run_for_cui(deps, my_cui="12345678", env="prod", access_token="tok", now=now_utc)

    assert isinstance(result, RunResult)
    assert result.processed == 1
    assert result.failures == 0
    [(_, zile)] = deps.anaf.list_calls  # type: ignore[attr-defined]
    assert zile == 60  # first run, no poll_state yet -> backfill 60 days


def test_run_for_cui_uses_zile_window_from_poll_state(deps: SyncDeps, now_utc: datetime) -> None:
    deps.anaf = FakeAnaf(list_response=[])  # type: ignore[assignment]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    upsert_poll_state(
        deps.db,
        cui="12345678",
        env="prod",
        last_polled_at=now_utc - timedelta(days=3),
    )

    run_for_cui(deps, my_cui="12345678", env="prod", access_token="tok", now=now_utc)

    [(_, zile)] = deps.anaf.list_calls  # type: ignore[attr-defined]
    assert zile == 4  # 3 days + 1 safety overlap


def test_run_for_cui_zile_override_forces_window(
    deps: SyncDeps, now_utc: datetime
) -> None:
    # An override forces the window even when poll_state would say otherwise.
    deps.anaf = FakeAnaf(list_response=[])  # type: ignore[assignment]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    upsert_poll_state(
        deps.db,
        cui="12345678",
        env="prod",
        last_polled_at=now_utc - timedelta(days=3),
    )

    run_for_cui(
        deps,
        my_cui="12345678",
        env="prod",
        access_token="tok",
        now=now_utc,
        zile_override=45,
    )

    [(_, zile)] = deps.anaf.list_calls  # type: ignore[attr-defined]
    assert zile == 45  # override wins over the 3-day poll_state delta


def test_run_for_cui_zile_override_is_clamped(deps: SyncDeps, now_utc: datetime) -> None:
    # Defense-in-depth clamp for non-CLI callers (CLI restricts to 1..60).
    from efactura_sync.sync import _zile_for_run

    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    assert _zile_for_run(deps, cui="12345678", env="prod", now=now_utc, override=100) == 60
    assert _zile_for_run(deps, cui="12345678", env="prod", now=now_utc, override=0) == 1


def test_run_for_cui_resume_pass_finishes_pending_rows(deps: SyncDeps, now_utc: datetime) -> None:
    deps.anaf = FakeAnaf(  # type: ignore[assignment]
        list_response=[],
        download_payload=_make_zip(UBL_FIXTURE),
    )
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    add_watched_counterparty(deps.db, my_cui="12345678", counterparty_cui="RO87654321", now=now_utc)
    # Pre-populate a row that has been processed once already.
    process_one_message(
        deps,
        my_cui="12345678",
        my_display_name="Acme",
        env="prod",
        access_token="tok",
        list_msg=_list_msg(),
        now=now_utc,
    )
    # Simulate "crash before email step" by clearing email_sent_at so the row is
    # again a pending candidate for the resume pass.
    deps.db.execute("UPDATE synced_messages SET email_sent_at=NULL WHERE msg_id='3001'")
    deps.db.commit()
    deps.mailer.sent.clear()  # type: ignore[attr-defined]

    result = run_for_cui(deps, my_cui="12345678", env="prod", access_token="tok", now=now_utc)

    # The pending row was finished (email re-sent).
    assert result.processed >= 1
    assert result.failures == 0
    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.email_sent_at is not None
    assert len(deps.mailer.sent) == 1  # type: ignore[attr-defined]


def test_run_for_cui_records_poll_state_on_success(deps: SyncDeps, now_utc: datetime) -> None:
    deps.anaf = FakeAnaf(list_response=[])  # type: ignore[assignment]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)

    run_for_cui(deps, my_cui="12345678", env="prod", access_token="tok", now=now_utc)

    from efactura_sync.storage.db import get_poll_state

    state = get_poll_state(deps.db, cui="12345678", env="prod")
    assert state is not None
    assert state.last_polled_at == now_utc


def test_run_for_cui_per_message_failure_in_poll_phase(deps: SyncDeps, now_utc: datetime) -> None:
    """When process_one_message blows up, that row counts as a failure but the
    run continues, last_error is set, and poll_state still advances."""

    class CrashOnDownload(FakeAnaf):
        def download(self, *, msg_id: str, access_token: str) -> bytes:
            raise RuntimeError("disk full")  # programming bug-ish

    deps.anaf = CrashOnDownload(list_response=[_list_msg()])  # type: ignore[assignment]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)

    result = run_for_cui(deps, my_cui="12345678", env="prod", access_token="tok", now=now_utc)

    # process_one_message handles EfacturaError-derived errors itself, but a raw
    # RuntimeError escapes — caught by run_for_cui's broad except.
    # Wait: process_one_message catches EfacturaError, not RuntimeError, so the
    # RuntimeError bubbles up out of process_one_message and into run_for_cui's
    # `except Exception`.
    assert result.processed == 0
    assert result.failures == 1

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.last_error is not None and row.last_error.startswith("poll:")

    # poll_state still advanced (we did successfully list, even if one row failed).
    from efactura_sync.storage.db import get_poll_state

    state = get_poll_state(deps.db, cui="12345678", env="prod")
    assert state is not None
    assert state.last_polled_at == now_utc


def test_run_for_cui_list_messages_error_does_not_advance_poll_state(
    deps: SyncDeps, now_utc: datetime
) -> None:
    """If anaf.list_messages itself raises, poll_state is NOT advanced.

    Rationale: we didn't successfully observe the new-message window, so the
    next run should re-attempt with the same `since` time.
    """
    from efactura_sync.errors import TransientError
    from efactura_sync.storage.db import get_poll_state

    class ListFails(FakeAnaf):
        def list_messages(self, *, cif: str, zile: int, access_token: str) -> list[ListMessage]:
            raise TransientError("anaf 503", status=503, body=b"")

    deps.anaf = ListFails()  # type: ignore[assignment]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)

    with pytest.raises(TransientError):
        run_for_cui(deps, my_cui="12345678", env="prod", access_token="tok", now=now_utc)

    # poll_state must remain absent (no row inserted by run_for_cui).
    state = get_poll_state(deps.db, cui="12345678", env="prod")
    assert state is None


def test_run_for_cui_idempotent_on_quick_rerun(deps: SyncDeps, now_utc: datetime) -> None:
    """Running twice in a row with the same message lists ANAF twice but
    INSERT ON CONFLICT DO NOTHING absorbs the duplicate."""
    deps.anaf = FakeAnaf(  # type: ignore[assignment]
        list_response=[_list_msg()],
        download_payload=_make_zip(UBL_FIXTURE),
    )
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    add_watched_counterparty(deps.db, my_cui="12345678", counterparty_cui="RO87654321", now=now_utc)

    r1 = run_for_cui(deps, my_cui="12345678", env="prod", access_token="tok", now=now_utc)
    r2 = run_for_cui(
        deps,
        my_cui="12345678",
        env="prod",
        access_token="tok",
        now=now_utc + timedelta(seconds=10),
    )

    # Both runs called list_messages (we don't try to dedup at the network layer).
    assert len(deps.anaf.list_calls) == 2  # type: ignore[attr-defined]
    # But the message was downloaded only once (second run sees the dedup row).
    assert deps.anaf.download_calls == ["3001"]  # type: ignore[attr-defined]
    # r1 sent the email; r2 sees email_sent_at is set and process_one_message
    # no-ops the email step rather than re-sending.
    assert r1.processed == 1
    assert r1.failures == 0
    assert r2.processed >= 0
    assert r2.failures == 0
    assert len(deps.mailer.sent) == 1  # type: ignore[attr-defined]


def test_email_send_failure_records_error_and_leaves_row_pending(
    deps: SyncDeps, now_utc: datetime
) -> None:
    """When mailer.send raises, last_error is set and email_sent_at stays NULL."""
    deps.anaf.download_payload = _make_zip(UBL_FIXTURE)  # type: ignore[attr-defined]
    deps.mailer = RecordingMailer(fail=True)  # type: ignore[assignment]
    add_monitored_cui(deps.db, cui="12345678", display_name=None, now=now_utc)
    add_watched_counterparty(deps.db, my_cui="12345678", counterparty_cui="RO87654321", now=now_utc)

    process_one_message(
        deps,
        my_cui="12345678",
        my_display_name=None,
        env="prod",
        access_token="tok",
        list_msg=_list_msg(),
        now=now_utc,
    )

    row = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row is not None
    assert row.zip_path is not None
    assert row.pdf_path is not None  # render succeeded
    assert row.email_sent_at is None
    assert row.email_skip_reason is None
    assert row.last_error is not None and row.last_error.startswith("email:")

    # Resume pass should retry the email step. Swap the mailer to a healthy one.
    deps.mailer = RecordingMailer(fail=False)  # type: ignore[assignment]
    deps.anaf = FakeAnaf(list_response=[])  # type: ignore[assignment]
    run_for_cui(deps, my_cui="12345678", env="prod", access_token="tok", now=now_utc)

    row2 = get_synced_message(deps.db, msg_id="3001", cui="12345678", env="prod")
    assert row2 is not None
    assert row2.email_sent_at is not None
    assert len(deps.mailer.sent) == 1  # type: ignore[attr-defined]
