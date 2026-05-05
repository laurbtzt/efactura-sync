from datetime import date
from pathlib import Path

from efactura_sync.storage.layout import (
    invoice_pdf_path,
    invoice_zip_path,
    message_zip_path,
)


def test_invoice_zip_received() -> None:
    p = invoice_zip_path(
        archive_root=Path("/a"),
        cui="12345678",
        msg_type="PRIMITA",
        issue_date=date(2026, 5, 4),
        msg_id="3001",
    )
    assert p == Path("/a/12345678/2026/05/received/archive/3001.zip")


def test_invoice_zip_sent() -> None:
    p = invoice_zip_path(
        archive_root=Path("/a"),
        cui="12345678",
        msg_type="TRIMISA",
        issue_date=date(2026, 1, 9),
        msg_id="42",
    )
    assert p == Path("/a/12345678/2026/01/sent/archive/42.zip")


def test_invoice_pdf_received() -> None:
    p = invoice_pdf_path(
        archive_root=Path("/a"),
        cui="12345678",
        msg_type="PRIMITA",
        issue_date=date(2026, 5, 4),
        msg_id="3001",
    )
    assert p == Path("/a/12345678/2026/05/received/pdf/3001.pdf")


def test_message_zip_for_erori_uses_creation_date() -> None:
    p = message_zip_path(
        archive_root=Path("/a"),
        cui="12345678",
        creation_date=date(2026, 5, 4),
        msg_id="9000",
    )
    assert p == Path("/a/12345678/messages/2026/05/9000.zip")
