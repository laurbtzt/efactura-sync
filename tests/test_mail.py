from datetime import date
from decimal import Decimal
from email import message_from_bytes
from typing import Any

import pytest

from efactura_sync.anaf.messages import InvoiceFields, ListMessage
from efactura_sync.mail import (
    EmailMessage,
    Mailer,
    render_erori_email,
    render_failure_email,
    render_mesaj_email,
    render_primita_email,
)


def _list_msg(**overrides: object) -> ListMessage:
    from datetime import UTC, datetime

    base = ListMessage(
        msg_id="3001",
        cif="12345678",
        data_creare_utc=datetime(2026, 5, 4, 8, 30, tzinfo=UTC),
        tip_raw="FACTURA PRIMITA",
        tip="PRIMITA",
        detalii="from RO87654321",
    )
    return ListMessage(**{**base.__dict__, **overrides})


def test_render_primita_email() -> None:
    fields = InvoiceFields(
        invoice_number="INV-00451",
        issue_date=date(2026, 5, 4),
        currency="RON",
        payable_amount=Decimal("1234.56"),
        supplier_cui="RO87654321",
        supplier_name="Furnizor X SRL",
        customer_cui="12345678",
    )
    email = render_primita_email(
        my_cui="12345678",
        my_display_name="Acme SRL",
        list_msg=_list_msg(),
        fields=fields,
        zip_attachment=("3001.zip", b"PK"),
        pdf_attachment=("3001.pdf", b"%PDF"),
        env="prod",
    )

    assert isinstance(email, EmailMessage)
    assert "[factură]" in email.subject
    assert "Acme SRL" in email.subject
    assert "Furnizor X SRL" in email.subject
    assert "INV-00451" in email.subject
    assert "2026-05-04" in email.subject
    assert "Sincronizare ANAF e-Factura — factură nouă primită" in email.body
    assert "1234.56 RON" in email.body
    assert email.message_id == "<3001.prod@efactura-sync>"
    assert ("3001.zip", b"PK") in email.attachments
    assert ("3001.pdf", b"%PDF") in email.attachments


def test_render_primita_email_pdf_pending_when_pdf_missing() -> None:
    fields = InvoiceFields(
        invoice_number="INV-X",
        issue_date=date(2026, 5, 4),
        currency="RON",
        payable_amount=None,
        supplier_cui="RO123",
        supplier_name=None,
        customer_cui="12345678",
    )
    email = render_primita_email(
        my_cui="12345678",
        my_display_name=None,
        list_msg=_list_msg(),
        fields=fields,
        zip_attachment=("3001.zip", b"PK"),
        pdf_attachment=None,
        env="prod",
    )
    assert "PDF: în curs de generare" in email.body
    assert ("3001.zip", b"PK") in email.attachments
    assert "Total:          —" in email.body or "Total:         —" in email.body


def test_render_erori_email_uses_eroare_prefix() -> None:
    list_msg = _list_msg(tip_raw="ERORI FACTURA", tip="ERORI", detalii="some error")
    email = render_erori_email(
        my_cui="12345678",
        my_display_name="Acme",
        list_msg=list_msg,
        zip_attachment=("3001.zip", b"PK"),
        env="prod",
    )
    assert email.subject.startswith("[eroare]")
    assert "ERORI FACTURA" in email.body
    assert "some error" in email.body
    assert email.attachments == [("3001.zip", b"PK")]


def test_render_mesaj_email_uses_notificare_prefix() -> None:
    list_msg = _list_msg(tip_raw="MESAJ_CUMPARATOR", tip="MESAJ", detalii="hi")
    email = render_mesaj_email(
        my_cui="12345678",
        my_display_name=None,
        list_msg=list_msg,
        zip_attachment=("3001.zip", b"PK"),
        env="prod",
    )
    assert email.subject.startswith("[notificare]")
    assert "MESAJ_CUMPARATOR" in email.body


def test_render_failure_email() -> None:
    email = render_failure_email(
        hostname="homepi",
        run_date=date(2026, 5, 4),
        cui_in_progress="12345678",
        step="descarcare",
        exception_type="TransientError",
        log_tail="line1\nline2",
        traceback="Traceback (most recent call last):\n...",
    )
    assert email.subject.startswith("[eroare-rulare]")
    assert "homepi" in email.subject
    assert "2026-05-04" in email.subject
    assert "12345678" in email.body
    assert "descarcare" in email.body
    assert "TransientError" in email.body
    assert email.attachments == [("traceback.txt", b"Traceback (most recent call last):\n...")]


class _RecordingSMTP:
    """Stand-in for smtplib.SMTP_SSL / SMTP that records what was sent."""

    instances: list[_RecordingSMTP] = []

    def __init__(self, host: str, port: int, *args: Any, **kwargs: Any) -> None:
        self.host = host
        self.port = port
        self.logged_in: tuple[str, str] | None = None
        self.starttls_called = False
        self.sent: list[tuple[str, list[str], bytes]] = []
        self.quit_called = False
        type(self).instances.append(self)

    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)

    def starttls(self) -> None:
        self.starttls_called = True

    def sendmail(self, from_addr: str, to_addrs: list[str], msg: bytes) -> None:
        self.sent.append((from_addr, to_addrs, msg))

    def quit(self) -> None:
        self.quit_called = True

    def __enter__(self) -> _RecordingSMTP:
        return self

    def __exit__(self, *args: object) -> None:
        self.quit()


def test_mailer_sends_with_implicit_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    _RecordingSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP_SSL", _RecordingSMTP)

    mailer = Mailer(
        host="smtp.example.com",
        port=465,
        tls="implicit",
        username="u",
        password="p",
        from_addr="from@example.com",
    )
    mailer.send(
        EmailMessage(
            subject="Test",
            body="hi",
            message_id="<1@efactura-sync>",
            attachments=[("a.txt", b"hello")],
        ),
        to_addr="to@example.com",
    )

    [smtp] = _RecordingSMTP.instances
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 465
    assert smtp.logged_in == ("u", "p")
    assert smtp.starttls_called is False
    [(frm, tos, raw)] = smtp.sent
    assert frm == "from@example.com"
    assert tos == ["to@example.com"]
    parsed = message_from_bytes(raw)
    assert parsed["Subject"] == "Test"
    assert parsed["Message-ID"] == "<1@efactura-sync>"
    payloads = parsed.get_payload()
    assert any(p.get_filename() == "a.txt" for p in payloads)


def test_mailer_starttls(monkeypatch: pytest.MonkeyPatch) -> None:
    _RecordingSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", _RecordingSMTP)

    mailer = Mailer(
        host="smtp.example.com",
        port=587,
        tls="starttls",
        username="u",
        password="p",
        from_addr="from@example.com",
    )
    mailer.send(
        EmailMessage(subject="x", body="y", message_id="<2@efactura-sync>", attachments=[]),
        to_addr="to@example.com",
    )

    [smtp] = _RecordingSMTP.instances
    assert smtp.port == 587
    assert smtp.starttls_called is True
