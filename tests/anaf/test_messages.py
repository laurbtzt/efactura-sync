from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pytest

from efactura_sync.anaf.messages import (
    InvoiceFields,
    ListMessage,
    classify_tip,
    extract_ubl_xml,
    parse_invoice_fields,
    parse_list_response,
)
from efactura_sync.errors import InvalidArchiveError

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ubl" / "primita_minimal.xml"


def test_classify_tip() -> None:
    assert classify_tip("FACTURA PRIMITA") == "PRIMITA"
    assert classify_tip("FACTURA TRIMISA") == "TRIMISA"
    assert classify_tip("ERORI FACTURA") == "ERORI"
    assert classify_tip("ANY OTHER STRING") == "MESAJ"


def test_parse_list_response() -> None:
    payload = {
        "mesaje": [
            {
                "id": "3001",
                "cif": "12345678",
                "data_creare": "202605041030",
                "tip": "FACTURA PRIMITA",
                "detalii": "supplier RO87654321",
            },
            {
                "id": "3002",
                "cif": "12345678",
                "data_creare": "202605041040",
                "tip": "FACTURA TRIMISA",
                "detalii": "for RO111",
            },
        ]
    }
    msgs = parse_list_response(payload)
    # data_creare "202605041030" is interpreted as Bucharest local (EEST = +03:00 in May),
    # so 10:30 local == 07:30 UTC.
    assert msgs == [
        ListMessage(
            msg_id="3001",
            cif="12345678",
            data_creare_utc=datetime(2026, 5, 4, 7, 30, tzinfo=UTC),
            tip_raw="FACTURA PRIMITA",
            tip="PRIMITA",
            detalii="supplier RO87654321",
        ),
        ListMessage(
            msg_id="3002",
            cif="12345678",
            data_creare_utc=datetime(2026, 5, 4, 7, 40, tzinfo=UTC),
            tip_raw="FACTURA TRIMISA",
            tip="TRIMISA",
            detalii="for RO111",
        ),
    ]


def test_extract_ubl_xml(tmp_path: Path) -> None:
    zip_path = tmp_path / "x.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.writestr("12345_invoice.xml", FIXTURE.read_bytes())
        zf.writestr("12345_signature.xml", b"<sig/>")

    xml = extract_ubl_xml(zip_path.read_bytes())
    assert b"<cbc:ID>INV-00451</cbc:ID>" in xml


def test_extract_ubl_xml_rejects_non_ubl(tmp_path: Path) -> None:
    zip_path = tmp_path / "x.zip"
    with ZipFile(zip_path, "w") as zf:
        zf.writestr("only_signature.xml", b"<sig/>")
    with pytest.raises(InvalidArchiveError):
        extract_ubl_xml(zip_path.read_bytes())


def test_extract_ubl_xml_rejects_bad_zip() -> None:
    with pytest.raises(InvalidArchiveError):
        extract_ubl_xml(b"not a zip")


def test_parse_invoice_fields() -> None:
    from datetime import date as _date

    fields = parse_invoice_fields(FIXTURE.read_bytes())
    assert fields == InvoiceFields(
        invoice_number="INV-00451",
        issue_date=_date(2026, 5, 4),
        currency="RON",
        payable_amount=Decimal("1234.56"),
        supplier_cui="RO87654321",
        supplier_name="Furnizor X SRL",
        customer_cui="12345678",
    )
