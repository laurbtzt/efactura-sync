"""Per-CUI sync orchestrator.

Walks each ANAF message through these steps:
  1. insert ledger row
  2. download ZIP -> atomic write
  3. (invoices only) extract UBL XML, render PDF -> atomic write
  4. apply email rules -> render + send (or mark a terminal skip reason)

Each step's success is recorded on a column of ``synced_messages``; a crash
mid-step leaves a column NULL that the next run finishes.
"""

import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Protocol, cast
from zoneinfo import ZoneInfo

from efactura_sync.anaf.messages import (
    InvoiceFields,
    ListMessage,
    MsgType,
    extract_ubl_xml,
    parse_invoice_fields,
)
from efactura_sync.errors import EfacturaError, InvalidArchiveError, RenderError
from efactura_sync.mail import (
    EmailMessage,
    render_erori_email,
    render_mesaj_email,
    render_primita_email,
)
from efactura_sync.storage import db as dbq
from efactura_sync.storage.files import FileStore
from efactura_sync.storage.layout import (
    invoice_pdf_path,
    invoice_zip_path,
    message_zip_path,
)
from efactura_sync.types import Env

_log = logging.getLogger(__name__)


class _AnafLike(Protocol):
    def list_messages(self, *, cif: str, zile: int, access_token: str) -> list[ListMessage]: ...

    def download(self, *, msg_id: str, access_token: str) -> bytes: ...


class _RendererLike(Protocol):
    def render(self, *, ubl_xml: bytes, standard: str = "FACT1") -> bytes: ...


class _MailerLike(Protocol):
    def send(self, msg: EmailMessage, *, to_addr: str) -> None: ...


@dataclass
class SyncDeps:
    anaf: _AnafLike
    renderer: _RendererLike
    files: FileStore
    db: sqlite3.Connection
    archive_root: Path
    mailer: _MailerLike
    to_addr: str


# ---- helpers --------------------------------------------------------------


def _bucharest_local_date(dt: datetime) -> date:
    return dt.astimezone(ZoneInfo("Europe/Bucharest")).date()


def _resolve_partition_date(list_msg: ListMessage, fields: InvoiceFields | None) -> date:
    if fields is not None and fields.issue_date is not None:
        return fields.issue_date
    return _bucharest_local_date(list_msg.data_creare_utc)


def _email_decision(
    deps: SyncDeps, *, my_cui: str, list_msg: ListMessage, counterparty_cui: str | None
) -> str | None:
    """Return ``None`` when the row should be emailed, else the terminal skip reason.

    Per spec §6:
      PRIMITA -> only email if supplier in watched_counterparties[my_cui], else
                 ``'filtered_by_watchlist'``. PRIMITA without a parsed
                 supplier_cui can't be allow-list-checked, so it's also
                 ``'filtered_by_watchlist'``.
      TRIMISA -> never email; ``'never_email_for_type'``.
      ERORI / MESAJ -> always email.
    """
    if list_msg.tip == "TRIMISA":
        return "never_email_for_type"
    if list_msg.tip == "PRIMITA":
        if counterparty_cui is None:
            return "filtered_by_watchlist"
        if not dbq.is_counterparty_watched(
            deps.db, my_cui=my_cui, counterparty_cui=counterparty_cui
        ):
            return "filtered_by_watchlist"
        return None  # send PRIMITA email
    # ERORI / MESAJ — always email.
    return None


# ---- main orchestrator ----------------------------------------------------


def process_one_message(
    deps: SyncDeps,
    *,
    my_cui: str,
    my_display_name: str | None,
    env: Env,
    access_token: str,
    list_msg: ListMessage,
    now: datetime,
) -> None:
    """Walk one ANAF message through download -> render -> email-decision -> ledger.

    Inserts the row if missing. On any non-fatal error the column for the
    failing step stays NULL and ``last_error`` is set; the next run retries.
    """
    # 1. ensure ledger row exists
    existing = dbq.get_synced_message(deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env)
    if existing is None:
        partition_date = _bucharest_local_date(list_msg.data_creare_utc)
        dbq.insert_synced_message(
            deps.db,
            dbq.SyncedMessage(
                msg_id=list_msg.msg_id,
                cui=my_cui,
                env=env,
                msg_type=list_msg.tip,
                counterparty_cui=None,
                issue_date=(partition_date if list_msg.tip in ("PRIMITA", "TRIMISA") else None),
                zip_path=None,
                pdf_path=None,
                email_sent_at=None,
                email_skip_reason=None,
                first_seen_at=now,
                last_attempt_at=now,
                last_error=None,
            ),
        )

    # 2. download ZIP if we don't have one yet
    row = dbq.get_synced_message(deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env)
    if row is None:
        raise RuntimeError(f"row vanished after insert: msg_id={list_msg.msg_id}")
    if row.zip_path is None:
        try:
            zip_bytes = deps.anaf.download(msg_id=list_msg.msg_id, access_token=access_token)
        except EfacturaError as e:
            dbq.update_attempt(
                deps.db,
                msg_id=list_msg.msg_id,
                cui=my_cui,
                env=env,
                error=f"download: {e}",
                now=now,
            )
            return
    else:
        zip_bytes = (deps.archive_root / row.zip_path).read_bytes()

    # 3. for invoices, parse UBL fields to learn issue_date + counterparty
    fields: InvoiceFields | None = None
    counterparty_cui: str | None = None
    partition_date = _bucharest_local_date(list_msg.data_creare_utc)
    ubl_xml: bytes | None = None
    if list_msg.tip in ("PRIMITA", "TRIMISA"):
        try:
            ubl_xml = extract_ubl_xml(zip_bytes)
            fields = parse_invoice_fields(ubl_xml)
            partition_date = _resolve_partition_date(list_msg, fields)
            counterparty_cui = (
                fields.supplier_cui if list_msg.tip == "PRIMITA" else fields.customer_cui
            )
        except InvalidArchiveError as e:
            dbq.update_attempt(
                deps.db,
                msg_id=list_msg.msg_id,
                cui=my_cui,
                env=env,
                error=f"parse: {e}",
                now=now,
            )
            return

    # 4. write ZIP to disk + record metadata
    if row.zip_path is None:
        if list_msg.tip in ("PRIMITA", "TRIMISA"):
            invoice_type: Literal["PRIMITA", "TRIMISA"] = list_msg.tip
            zip_target = invoice_zip_path(
                archive_root=deps.archive_root,
                cui=my_cui,
                msg_type=invoice_type,
                issue_date=partition_date,
                msg_id=list_msg.msg_id,
            )
        else:
            zip_target = message_zip_path(
                archive_root=deps.archive_root,
                cui=my_cui,
                creation_date=partition_date,
                msg_id=list_msg.msg_id,
            )
        deps.files.atomic_write(zip_target, zip_bytes)
        rel_zip = str(zip_target.relative_to(deps.archive_root))
        # Backfill issue_date + counterparty_cui (we only learned them after
        # parsing the XML).
        dbq.finalize_zip_write(
            deps.db,
            msg_id=list_msg.msg_id,
            cui=my_cui,
            env=env,
            zip_path=rel_zip,
            counterparty_cui=counterparty_cui,
            issue_date=(partition_date if list_msg.tip in ("PRIMITA", "TRIMISA") else None),
            now=now,
        )
        row = dbq.get_synced_message(deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env)
        if row is None:
            raise RuntimeError(f"row vanished after finalize_zip_write: msg_id={list_msg.msg_id}")

    # 5. render PDF for invoices (if not already done)
    if list_msg.tip in ("PRIMITA", "TRIMISA") and row.pdf_path is None:
        try:
            if ubl_xml is None:
                raise RuntimeError(
                    f"ubl_xml missing for invoice render: msg_id={list_msg.msg_id}"
                )
            pdf_bytes = deps.renderer.render(ubl_xml=ubl_xml)
            invoice_type2: Literal["PRIMITA", "TRIMISA"] = list_msg.tip
            pdf_target = invoice_pdf_path(
                archive_root=deps.archive_root,
                cui=my_cui,
                msg_type=invoice_type2,
                issue_date=partition_date,
                msg_id=list_msg.msg_id,
            )
            deps.files.atomic_write(pdf_target, pdf_bytes)
            rel_pdf = str(pdf_target.relative_to(deps.archive_root))
            dbq.update_pdf_path(
                deps.db,
                msg_id=list_msg.msg_id,
                cui=my_cui,
                env=env,
                pdf_path=rel_pdf,
                now=now,
            )
        except RenderError as e:
            dbq.update_attempt(
                deps.db,
                msg_id=list_msg.msg_id,
                cui=my_cui,
                env=env,
                error=f"render: {e}",
                now=now,
            )
            # Don't mark email step until render succeeds. Spec §6.3 allows
            # ZIP-only PRIMITA email on render failure; we keep it tighter for
            # now and let the resume path retry render. If render keeps
            # failing, manual intervention via /replay is needed.
            return

    # 6. email decision: skip with a terminal reason or render + send.
    row = dbq.get_synced_message(deps.db, msg_id=list_msg.msg_id, cui=my_cui, env=env)
    if row is None:
        raise RuntimeError(f"row vanished before email step: msg_id={list_msg.msg_id}")
    if row.email_sent_at is not None or row.email_skip_reason is not None:
        return

    skip_reason = _email_decision(
        deps,
        my_cui=my_cui,
        list_msg=list_msg,
        counterparty_cui=row.counterparty_cui,
    )
    if skip_reason is not None:
        dbq.mark_email_skipped(
            deps.db,
            msg_id=list_msg.msg_id,
            cui=my_cui,
            env=env,
            reason=skip_reason,
            now=now,
        )
        return

    if row.zip_path is None:
        raise RuntimeError(f"zip_path missing before email send: msg_id={list_msg.msg_id}")
    zip_path_abs = deps.archive_root / row.zip_path
    zip_attachment = (Path(row.zip_path).name, zip_path_abs.read_bytes())

    if list_msg.tip == "PRIMITA":
        pdf_attachment: tuple[str, bytes] | None = None
        if row.pdf_path is not None:
            pdf_path_abs = deps.archive_root / row.pdf_path
            pdf_attachment = (Path(row.pdf_path).name, pdf_path_abs.read_bytes())
        if fields is None:
            raise RuntimeError(f"fields missing for PRIMITA email: msg_id={list_msg.msg_id}")
        email = render_primita_email(
            my_cui=my_cui,
            my_display_name=my_display_name,
            list_msg=list_msg,
            fields=fields,
            zip_attachment=zip_attachment,
            pdf_attachment=pdf_attachment,
            env=env,
        )
    elif list_msg.tip == "ERORI":
        email = render_erori_email(
            my_cui=my_cui,
            my_display_name=my_display_name,
            list_msg=list_msg,
            zip_attachment=zip_attachment,
            env=env,
        )
    elif list_msg.tip == "MESAJ":
        email = render_mesaj_email(
            my_cui=my_cui,
            my_display_name=my_display_name,
            list_msg=list_msg,
            zip_attachment=zip_attachment,
            env=env,
        )
    else:
        # TRIMISA always returns a skip reason from _email_decision; can't reach here.
        raise AssertionError(f"unexpected tip in send branch: {list_msg.tip!r}")

    try:
        deps.mailer.send(email, to_addr=deps.to_addr)
    except Exception as e:
        dbq.update_attempt(
            deps.db,
            msg_id=list_msg.msg_id,
            cui=my_cui,
            env=env,
            error=f"email: {e}",
            now=now,
        )
        return
    dbq.mark_email_sent(
        deps.db,
        msg_id=list_msg.msg_id,
        cui=my_cui,
        env=env,
        sent_at=now,
    )


# ---- per-CUI run ----------------------------------------------------------


@dataclass(frozen=True)
class RunResult:
    processed: int
    failures: int


def _zile_for_run(deps: SyncDeps, *, cui: str, env: Env, now: datetime) -> int:
    state = dbq.get_poll_state(deps.db, cui=cui, env=env)
    if state is None:
        return 1
    delta_days = math.ceil((now - state.last_polled_at).total_seconds() / 86400) + 1
    return max(1, min(60, delta_days))


def run_for_cui(
    deps: SyncDeps,
    *,
    my_cui: str,
    env: Env,
    access_token: str,
    now: datetime,
) -> RunResult:
    """Run a full daily sync for a single CUI.

    Steps: resume any pending rows; list new messages; process each new one.
    Records ``poll_state`` after both phases complete. Per-message errors are
    absorbed (recorded via ``update_attempt``, counted in ``failures``).

    Raises:
        Whatever ``anaf.list_messages`` raises if the new-message poll fails
        at the network/HTTP level. In that case ``last_polled_at`` is NOT
        advanced — the caller can retry next day. Per-message exceptions
        inside the resume / new-message loops do NOT propagate; they are
        recorded on the row's ``last_error`` and counted in ``failures``.
    """
    _log.info("starting run cui=%s env=%s", my_cui, env)
    processed = 0
    failures = 0
    my_display_name = dbq.get_monitored_cui_display_name(deps.db, cui=my_cui)

    # Resume pass: re-process pending rows. We rebuild a synthetic ListMessage
    # from each pending row so process_one_message can drive its state machine.
    pending_rows = list(dbq.find_pending_rows(deps.db, cui=my_cui, env=env))
    _log.info("resume: %d pending row(s)", len(pending_rows))
    for row in pending_rows:
        # data_creare_utc=row.first_seen_at: not the true ANAF creation timestamp,
        # but functionally a no-op on the resume path — process_one_message only
        # uses it for the partition-date fallback when XML parsing fails, and a
        # row in find_pending_rows already has zip_path set (or will be re-downloaded
        # on this iteration), so the partition path is recomputed from the parsed
        # issue_date rather than this synthetic timestamp.
        list_msg = ListMessage(
            msg_id=row.msg_id,
            cif=my_cui,
            data_creare_utc=row.first_seen_at,
            tip_raw=row.msg_type,
            tip=cast(MsgType, row.msg_type),
            detalii="",
        )
        try:
            process_one_message(
                deps,
                my_cui=my_cui,
                my_display_name=my_display_name,
                env=env,
                access_token=access_token,
                list_msg=list_msg,
                now=now,
            )
            processed += 1
        # Catch broad Exception (not just EfacturaError) so a programming bug in
        # process_one_message becomes one recorded per-row failure rather than
        # aborting the whole daily run. KeyboardInterrupt / SystemExit pass
        # through (they're BaseException, not Exception).
        except Exception as e:
            failures += 1
            dbq.update_attempt(
                deps.db,
                msg_id=row.msg_id,
                cui=my_cui,
                env=env,
                error=f"resume: {e}",
                now=now,
            )

    # New-message poll
    zile = _zile_for_run(deps, cui=my_cui, env=env, now=now)
    new_msgs = deps.anaf.list_messages(cif=my_cui, zile=zile, access_token=access_token)
    _log.info("poll: zile=%d new=%d", zile, len(new_msgs))
    for msg in new_msgs:
        try:
            process_one_message(
                deps,
                my_cui=my_cui,
                my_display_name=my_display_name,
                env=env,
                access_token=access_token,
                list_msg=msg,
                now=now,
            )
            processed += 1
        # Catch broad Exception (not just EfacturaError) so a programming bug in
        # process_one_message becomes one recorded per-row failure rather than
        # aborting the whole daily run. KeyboardInterrupt / SystemExit pass
        # through (they're BaseException, not Exception).
        except Exception as e:
            failures += 1
            dbq.update_attempt(
                deps.db,
                msg_id=msg.msg_id,
                cui=my_cui,
                env=env,
                error=f"poll: {e}",
                now=now,
            )

    dbq.upsert_poll_state(deps.db, cui=my_cui, env=env, last_polled_at=now)
    _log.info("done cui=%s processed=%d failures=%d", my_cui, processed, failures)
    return RunResult(processed=processed, failures=failures)
