"""Render UBL XML to PDF via ANAF's hosted xmltopdf service."""

import logging

import httpx

from efactura_sync import USER_AGENT
from efactura_sync.errors import RenderError
from efactura_sync.types import Env

_BASE_URLS: dict[Env, str] = {
    "prod": "https://webservicesp.anaf.ro/prod/FCTEL/rest/transformare",
    "test": "https://webservicesp.anaf.ro/test/FCTEL/rest/transformare",
}

_log = logging.getLogger(__name__)


class PdfRenderer:
    """POST UBL XML to ANAF's xmltopdf endpoint and return the rendered PDF.

    Caller owns the lifecycle of ``http``; close it via ``with httpx.Client() as ...``.
    """

    def __init__(self, http: httpx.Client, env: Env = "prod") -> None:
        self._http = http
        self._base = _BASE_URLS[env]

    def render(self, *, ubl_xml: bytes, standard: str = "FACT1") -> bytes:
        _log.debug("xmltopdf standard=%s xml_bytes=%d", standard, len(ubl_xml))
        resp = self._http.post(
            f"{self._base}/{standard}",
            content=ubl_xml,
            headers={
                "Content-Type": "text/plain",
                "User-Agent": USER_AGENT,
            },
            timeout=30.0,
        )
        if resp.status_code != 200:
            snippet = resp.content[:200].decode("utf-8", "replace")
            raise RenderError(f"xmltopdf returned HTTP {resp.status_code}: {snippet}")
        if not resp.content.startswith(b"%PDF"):
            raise RenderError("xmltopdf response is not a PDF")
        _log.info("rendered PDF %d bytes", len(resp.content))
        return resp.content
