"""Email rendering (Romanian) and SMTP delivery.

This module is split in two layers:
  * Pure rendering functions return :class:`EmailMessage` values.
  * The :class:`Mailer` class owns the SMTP connection and side-effects.
"""

import smtplib
from dataclasses import dataclass, field
from datetime import date
from email.message import EmailMessage as _StdlibEmailMessage
from typing import Literal

from efactura_sync.anaf.messages import InvoiceFields, ListMessage
from efactura_sync.types import Env


@dataclass(frozen=True)
class EmailMessage:
    subject: str
    body: str
    message_id: str
    attachments: list[tuple[str, bytes]] = field(default_factory=list)


def _msg_id(*, msg_id: str, env: str) -> str:
    return f"<{msg_id}.{env}@efactura-sync>"


def _display_or_dash(value: str | None) -> str:
    return value if value else "—"


def _fmt_amount(amount: object, currency: str | None) -> str:
    if amount is None:
        return "—"
    cur = currency or ""
    return f"{amount} {cur}".strip()


def _supplier_label(fields: InvoiceFields) -> str:
    name = fields.supplier_name or "—"
    cui = fields.supplier_cui or "—"
    return f"{cui} ({name})"


def render_primita_email(
    *,
    my_cui: str,
    my_display_name: str | None,
    list_msg: ListMessage,
    fields: InvoiceFields,
    zip_attachment: tuple[str, bytes],
    pdf_attachment: tuple[str, bytes] | None,
    env: Env,
) -> EmailMessage:
    me_label = my_display_name or my_cui
    supplier_label_short = fields.supplier_name or fields.supplier_cui or "—"
    supplier_cui = fields.supplier_cui or "—"
    inv_no = _display_or_dash(fields.invoice_number)
    issue = fields.issue_date.isoformat() if fields.issue_date else "—"

    subject = (
        f"[factură] {me_label} · {supplier_label_short} (CUI {supplier_cui}) · {inv_no} · {issue}"
    )

    pdf_line = "Atașamente: arhiva ZIP semnată, PDF generat."
    attachments: list[tuple[str, bytes]] = [zip_attachment]
    if pdf_attachment is not None:
        attachments.append(pdf_attachment)
    else:
        pdf_line = (
            "Atașament: arhiva ZIP semnată.\n"
            "PDF: în curs de generare — se va retrimite la următoarea rulare."
        )

    body = (
        "Sincronizare ANAF e-Factura — factură nouă primită\n"
        "\n"
        f"CUI propriu:    {my_cui} ({my_display_name or '—'})\n"
        f"Furnizor:       {_supplier_label(fields)}\n"
        f"Număr factură:  {inv_no}\n"
        f"Data emiterii:  {issue}\n"
        f"Total:          {_fmt_amount(fields.payable_amount, fields.currency)}\n"
        f"ID mesaj ANAF:  {list_msg.msg_id}\n"
        "\n"
        f"{pdf_line}\n"
    )

    return EmailMessage(
        subject=subject,
        body=body,
        message_id=_msg_id(msg_id=list_msg.msg_id, env=env),
        attachments=attachments,
    )


def render_erori_email(
    *,
    my_cui: str,
    my_display_name: str | None,
    list_msg: ListMessage,
    zip_attachment: tuple[str, bytes],
    env: Env,
) -> EmailMessage:
    me_label = my_display_name or my_cui
    subject = f"[eroare] {me_label} · mesaj {list_msg.msg_id} · {list_msg.tip_raw}"
    body = (
        "Sincronizare ANAF e-Factura — eroare primită de la ANAF\n"
        "\n"
        f"CUI propriu:   {my_cui} ({my_display_name or '—'})\n"
        f"ID mesaj ANAF: {list_msg.msg_id}\n"
        f"Tip mesaj:     {list_msg.tip_raw}\n"
        f"Detalii ANAF:  {list_msg.detalii}\n"
        "\n"
        "Atașament: arhiva ZIP originală.\n"
    )
    return EmailMessage(
        subject=subject,
        body=body,
        message_id=_msg_id(msg_id=list_msg.msg_id, env=env),
        attachments=[zip_attachment],
    )


def render_mesaj_email(
    *,
    my_cui: str,
    my_display_name: str | None,
    list_msg: ListMessage,
    zip_attachment: tuple[str, bytes],
    env: Env,
) -> EmailMessage:
    me_label = my_display_name or my_cui
    subject = f"[notificare] {me_label} · mesaj {list_msg.msg_id} · {list_msg.tip_raw}"
    body = (
        "Sincronizare ANAF e-Factura — notificare nouă\n"
        "\n"
        f"CUI propriu:   {my_cui} ({my_display_name or '—'})\n"
        f"ID mesaj ANAF: {list_msg.msg_id}\n"
        f"Tip mesaj:     {list_msg.tip_raw}\n"
        f"Detalii ANAF:  {list_msg.detalii}\n"
        "\n"
        "Atașament: arhiva ZIP originală.\n"
    )
    return EmailMessage(
        subject=subject,
        body=body,
        message_id=_msg_id(msg_id=list_msg.msg_id, env=env),
        attachments=[zip_attachment],
    )


def render_failure_email(
    *,
    hostname: str,
    run_date: date,
    cui_in_progress: str | None,
    step: str | None,
    exception_type: str,
    log_tail: str,
    traceback: str,
) -> EmailMessage:
    subject = f"[eroare-rulare] efactura-sync — {run_date.isoformat()} — {hostname}"
    body = (
        "Sincronizare ANAF e-Factura — rulare eșuată\n"
        "\n"
        f"Host:           {hostname}\n"
        f"Data:           {run_date.isoformat()}\n"
        f"CUI în lucru:   {cui_in_progress or '—'}\n"
        f"Pas eșuat:      {step or '—'}\n"
        f"Tip excepție:   {exception_type}\n"
        "\n"
        "Ultimele rânduri din log:\n"
        f"{log_tail}\n"
    )
    return EmailMessage(
        subject=subject,
        body=body,
        message_id=f"<failure-{run_date.isoformat()}-{hostname}@efactura-sync>",
        attachments=[("traceback.txt", traceback.encode("utf-8"))],
    )


class Mailer:
    """SMTP sender. One connection per :meth:`send` call (callers may reuse)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        tls: Literal["implicit", "starttls"],
        username: str,
        password: str,
        from_addr: str,
    ) -> None:
        self._host = host
        self._port = port
        self._tls = tls
        self._username = username
        self._password = password
        self._from = from_addr

    def _connect(self) -> smtplib.SMTP:
        if self._tls == "implicit":
            return smtplib.SMTP_SSL(self._host, self._port)
        return smtplib.SMTP(self._host, self._port)

    def send(self, msg: EmailMessage, *, to_addr: str) -> None:
        std = _StdlibEmailMessage()
        std["Subject"] = msg.subject
        std["From"] = self._from
        std["To"] = to_addr
        std["Message-ID"] = msg.message_id
        std.set_content(msg.body, charset="utf-8")
        for filename, data in msg.attachments:
            std.add_attachment(
                data,
                maintype="application",
                subtype="octet-stream",
                filename=filename,
            )

        with self._connect() as smtp:
            if self._tls == "starttls":
                smtp.starttls()
            smtp.login(self._username, self._password)
            smtp.sendmail(self._from, [to_addr], std.as_bytes())
