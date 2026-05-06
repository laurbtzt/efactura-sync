"""Render UBL XML to PDF via ANAF's hosted xmltopdf service."""

import httpx

from efactura_sync import __version__
from efactura_sync.errors import RenderError

_XMLTOPDF_BASE = "https://webservicesp.anaf.ro/prod/FCTEL/rest/transformare"
_USER_AGENT = f"efactura-sync/{__version__}"


class PdfRenderer:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def render(self, *, ubl_xml: bytes, standard: str = "FACT1") -> bytes:
        resp = self._http.post(
            f"{_XMLTOPDF_BASE}/{standard}",
            content=ubl_xml,
            headers={
                "Content-Type": "text/plain",
                "User-Agent": _USER_AGENT,
            },
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise RenderError(f"xmltopdf returned HTTP {resp.status_code}: {resp.text[:200]}")
        if not resp.content.startswith(b"%PDF"):
            raise RenderError("xmltopdf response is not a PDF")
        return resp.content
