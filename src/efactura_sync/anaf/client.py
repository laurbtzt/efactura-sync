"""HTTP wrapper for the read-side ANAF e-Factura endpoints."""

import json

import httpx

from efactura_sync import USER_AGENT
from efactura_sync.anaf.messages import ListMessage, parse_list_response
from efactura_sync.errors import PermanentError, TransientError
from efactura_sync.types import Env

_BASE_URLS: dict[Env, str] = {
    "prod": "https://api.anaf.ro/prod/FCTEL/rest",
    "test": "https://api.anaf.ro/test/FCTEL/rest",
}


# TODO(retry-loop): honor Retry-After when implementing the §7.5 retry schedule
#   (`[1s, 5s, 30s, 5m]`). Currently we raise once and rely on the daily cron
#   to retry tomorrow.
def _classify(response: httpx.Response) -> None:
    """Raise TransientError for 5xx/429, PermanentError for 4xx."""
    if response.status_code == 429 or 500 <= response.status_code < 600:
        raise TransientError(
            f"transient HTTP {response.status_code}",
            status=response.status_code,
            body=response.content,
        )
    if 400 <= response.status_code < 500:
        raise PermanentError(
            f"permanent HTTP {response.status_code}",
            status=response.status_code,
            body=response.content,
        )


class AnafClient:
    """HTTP client for ANAF's read-side e-Factura endpoints.

    Caller owns the lifecycle of ``http``; close it via ``with httpx.Client() as ...``.
    Use one shared ``httpx.Client`` for both this class and ``PdfRenderer`` so a
    single connection pool serves all ANAF traffic.
    """

    def __init__(self, http: httpx.Client, env: Env) -> None:
        self._http = http
        self._base = _BASE_URLS[env]

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": USER_AGENT,
        }

    def list_messages(self, *, cif: str, zile: int, access_token: str) -> list[ListMessage]:
        zile_clamped = max(1, min(60, zile))
        resp = self._http.get(
            f"{self._base}/listaMesajeFactura",
            params={"cif": cif, "zile": zile_clamped},
            headers=self._headers(access_token),
            timeout=30.0,
        )
        _classify(resp)
        try:
            payload = resp.json()
        except (ValueError, json.JSONDecodeError) as e:
            raise PermanentError(
                f"listamesaje returned non-JSON payload: {e}",
                status=resp.status_code,
                body=resp.content,
            ) from e
        if not isinstance(payload, dict):
            raise PermanentError(
                "listamesaje returned non-dict payload",
                status=resp.status_code,
                body=resp.content,
            )
        return parse_list_response(payload)

    def download(self, *, msg_id: str, access_token: str) -> bytes:
        resp = self._http.get(
            f"{self._base}/descarcare",
            params={"id": msg_id},
            headers=self._headers(access_token),
            timeout=60.0,
        )
        _classify(resp)
        return resp.content
