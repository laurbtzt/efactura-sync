"""Compute archive paths from a small set of inputs."""

from datetime import date
from pathlib import Path
from typing import Literal

InvoiceType = Literal["PRIMITA", "TRIMISA"]
_DIR_BY_TYPE: dict[str, str] = {"PRIMITA": "received", "TRIMISA": "sent"}


def invoice_zip_path(
    *,
    archive_root: Path,
    cui: str,
    msg_type: InvoiceType,
    issue_date: date,
    msg_id: str,
) -> Path:
    return (
        archive_root
        / cui
        / f"{issue_date.year:04d}"
        / f"{issue_date.month:02d}"
        / _DIR_BY_TYPE[msg_type]
        / "archive"
        / f"{msg_id}.zip"
    )


def invoice_pdf_path(
    *,
    archive_root: Path,
    cui: str,
    msg_type: InvoiceType,
    issue_date: date,
    msg_id: str,
) -> Path:
    return (
        archive_root
        / cui
        / f"{issue_date.year:04d}"
        / f"{issue_date.month:02d}"
        / _DIR_BY_TYPE[msg_type]
        / "pdf"
        / f"{msg_id}.pdf"
    )


def message_zip_path(
    *,
    archive_root: Path,
    cui: str,
    creation_date: date,
    msg_id: str,
) -> Path:
    return (
        archive_root
        / cui
        / "messages"
        / f"{creation_date.year:04d}"
        / f"{creation_date.month:02d}"
        / f"{msg_id}.zip"
    )
