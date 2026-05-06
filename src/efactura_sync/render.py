"""Render UBL XML to PDF via ANAF's hosted xmltopdf service."""

from typing import Literal

import httpx

from efactura_sync import __version__
from efactura_sync.errors import RenderError

Env = Literal["prod", "test"]

_BASE_URLS: dict[Env, str] = {
    "prod": "https://webservicesp.anaf.ro/prod/FCTEL/rest/transformare",
    "test": "https://webservicesp.anaf.ro/test/FCTEL/rest/transformare",
}

_USER_AGENT = f"efactura-sync/{__version__}"


class PdfRenderer:
    """POST UBL XML to ANAF's xmltopdf endpoint and return the rendered PDF.

    Caller owns the lifecycle of ``http``; close it via ``with httpx.Client() as ...``.
    """

    def __init__(self, http: httpx.Client, env: Env = "prod") -> None:
        self._http = http
        self._base = _BASE_URLS[env]

    def render(self, *, ubl_xml: bytes, standard: str = "FACT1") -> bytes:
        resp = self._http.post(
            f"{self._base}/{standard}",
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
