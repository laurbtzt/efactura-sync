"""Decode listamesaje JSON and parse UBL XML invoices."""

import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from lxml import etree

from efactura_sync.errors import InvalidArchiveError

NS = {
    "ubl": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}

# Hardened XML parser: never resolve external entities, never fetch network
# resources, never accept multi-gigabyte trees. Used for both ZIP-extracted
# UBL XML and signed-supplier UBL XML. ANAF signs the ZIP, but the XML inside
# is authored by the *supplier* — treat it as untrusted input.
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)

MsgType = Literal["PRIMITA", "TRIMISA", "ERORI", "MESAJ"]


@dataclass(frozen=True)
class ListMessage:
    msg_id: str
    cif: str
    data_creare_utc: datetime
    tip_raw: str
    tip: MsgType
    detalii: str


@dataclass(frozen=True)
class InvoiceFields:
    invoice_number: str | None
    issue_date: date | None
    currency: str | None
    payable_amount: Decimal | None
    supplier_cui: str | None
    supplier_name: str | None
    customer_cui: str | None


def classify_tip(raw: str) -> MsgType:
    upper = raw.upper().strip()
    if upper == "FACTURA PRIMITA":
        return "PRIMITA"
    if upper == "FACTURA TRIMISA":
        return "TRIMISA"
    if upper == "ERORI FACTURA":
        return "ERORI"
    return "MESAJ"


def _parse_data_creare(raw: str) -> datetime:
    """ANAF returns YYYYMMDDHHMM in Europe/Bucharest local time.

    We convert to UTC. Bucharest is UTC+2 in winter, UTC+3 in summer (DST).

    Note on DST autumn-ambiguity: on the last Sunday of October the local
    clock 03:00–04:00 occurs twice. ``naive.replace(tzinfo=...)`` picks
    ``fold=0`` (the first occurrence, EEST = +03:00) — i.e. the pre-DST
    instant. Acceptable since ANAF data_creare resolution is per-minute and
    the at-most off-by-one-hour skew only affects timestamps emitted in the
    one-hour overlap window, once per year.
    """
    naive = datetime.strptime(raw, "%Y%m%d%H%M")
    local = naive.replace(tzinfo=ZoneInfo("Europe/Bucharest"))
    return local.astimezone(UTC)


def parse_list_response(payload: dict[str, Any]) -> list[ListMessage]:
    items = payload.get("mesaje", []) or []
    out: list[ListMessage] = []
    for it in items:
        tip_raw = str(it.get("tip", ""))
        out.append(
            ListMessage(
                msg_id=str(it["id"]),
                cif=str(it["cif"]),
                data_creare_utc=_parse_data_creare(str(it["data_creare"])),
                tip_raw=tip_raw,
                tip=classify_tip(tip_raw),
                detalii=str(it.get("detalii", "")),
            )
        )
    return out


def extract_ubl_xml(zip_bytes: bytes) -> bytes:
    """Return the UBL Invoice XML from a signed ZIP. Raise on invalid input."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise InvalidArchiveError(f"not a valid zip: {e}") from e

    with zf:
        for name in zf.namelist():
            if not name.lower().endswith(".xml"):
                continue
            if "signature" in name.lower() or "semnatura" in name.lower():
                continue
            data = zf.read(name)
            try:
                root = etree.fromstring(data, _PARSER)
            except etree.XMLSyntaxError:
                continue
            if root.tag == f"{{{NS['ubl']}}}Invoice":
                return data
    raise InvalidArchiveError("ZIP does not contain a UBL Invoice xml")


def _text(root: etree._Element, xpath: str) -> str | None:
    nodes = cast(list[Any], root.xpath(xpath, namespaces=NS))
    if not nodes:
        return None
    first = nodes[0]
    if isinstance(first, etree._Element):
        text = first.text
        return text.strip() if text else None
    return str(first).strip()


def parse_invoice_fields(ubl_xml: bytes) -> InvoiceFields:
    try:
        root = etree.fromstring(ubl_xml, _PARSER)
    except etree.XMLSyntaxError as e:
        raise InvalidArchiveError(f"invalid UBL XML: {e}") from e

    inv_no = _text(root, "/ubl:Invoice/cbc:ID")
    issue_raw = _text(root, "/ubl:Invoice/cbc:IssueDate")
    currency = _text(root, "/ubl:Invoice/cbc:DocumentCurrencyCode")
    amount = _text(root, "/ubl:Invoice/cac:LegalMonetaryTotal/cbc:PayableAmount")
    supplier_cui = _text(
        root,
        "/ubl:Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:CompanyID",
    ) or _text(
        root,
        "/ubl:Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID",
    )
    supplier_name = _text(
        root,
        "/ubl:Invoice/cac:AccountingSupplierParty/cac:Party"
        "/cac:PartyLegalEntity/cbc:RegistrationName",
    )
    customer_cui = _text(
        root,
        "/ubl:Invoice/cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:CompanyID",
    )

    return InvoiceFields(
        invoice_number=inv_no,
        issue_date=date.fromisoformat(issue_raw) if issue_raw else None,
        currency=currency,
        payable_amount=Decimal(amount) if amount else None,
        supplier_cui=supplier_cui,
        supplier_name=supplier_name,
        customer_cui=customer_cui,
    )
