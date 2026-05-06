from typing import Any

import httpx
import pytest

from efactura_sync.anaf.client import AnafClient
from efactura_sync.errors import PermanentError, TransientError


def _client(handler: httpx.MockTransport) -> AnafClient:
    return AnafClient(http=httpx.Client(transport=handler), env="prod")


def test_list_messages_sends_correct_query_and_auth() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"mesaje": []})

    client = _client(httpx.MockTransport(handler))
    out = client.list_messages(cif="12345678", zile=1, access_token="tok")

    assert out == []
    assert "cif=12345678" in captured["url"]
    assert "zile=1" in captured["url"]
    assert "/listaMesajeFactura" in captured["url"]
    assert captured["auth"] == "Bearer tok"


def test_list_messages_clamps_zile_to_60() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"mesaje": []})

    client = _client(httpx.MockTransport(handler))
    client.list_messages(cif="12345678", zile=999, access_token="tok")
    assert "zile=60" in captured["url"]


def test_download_returns_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"PK\x03\x04zipbytes")

    client = _client(httpx.MockTransport(handler))
    out = client.download(msg_id="3001", access_token="tok")
    assert out == b"PK\x03\x04zipbytes"


def test_5xx_raises_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream")

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(TransientError):
        client.list_messages(cif="12345678", zile=1, access_token="tok")


def test_4xx_raises_permanent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad token")

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(PermanentError):
        client.download(msg_id="3001", access_token="tok")
