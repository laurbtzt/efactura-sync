import httpx
import pytest

from efactura_sync.errors import RenderError
from efactura_sync.render import PdfRenderer


def test_render_posts_xml_and_returns_pdf() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        captured["content_type"] = request.headers.get("Content-Type")
        return httpx.Response(200, content=b"%PDF-1.7 fake")

    renderer = PdfRenderer(http=httpx.Client(transport=httpx.MockTransport(handler)))
    pdf = renderer.render(ubl_xml=b"<Invoice/>", standard="FACT1")

    assert pdf == b"%PDF-1.7 fake"
    assert str(captured["url"]).endswith("/transformare/FACT1")
    assert captured["body"] == b"<Invoice/>"
    assert "text/plain" in str(captured["content_type"])


def test_render_rejects_non_pdf_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>error page</html>")

    renderer = PdfRenderer(http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RenderError, match="not a PDF"):
        renderer.render(ubl_xml=b"<Invoice/>")


def test_render_5xx_raises_render_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    renderer = PdfRenderer(http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RenderError):
        renderer.render(ubl_xml=b"<Invoice/>")


def test_render_uses_test_env_url() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, content=b"%PDF-1.7 fake")

    renderer = PdfRenderer(
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        env="test",
    )
    renderer.render(ubl_xml=b"<Invoice/>")

    assert "/test/FCTEL/rest/transformare/" in str(captured["url"])
