from datetime import date
from decimal import Decimal

from efactura_sync.anaf.messages import InvoiceFields, ListMessage
from efactura_sync.mail import (
    EmailMessage,
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
