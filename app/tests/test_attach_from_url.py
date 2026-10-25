import asyncio

import httpx2
import pytest
from fastmcp import Client

import server

PDF = b"%PDF-1.4 receipt"
SHARE_URL = "https://paperless.example.com/share/abc"


def _clients(captured: dict) -> tuple[httpx2.AsyncClient, httpx2.AsyncClient]:
    """InvoiceNinja client recording the upload, and a fetcher serving the PDF."""
    def invoiceninja(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/v1/expenses/exp123/upload" and request.method == "POST":
            captured["body"] = request.content
            captured["token"] = request.headers.get("x-api-token")
            return httpx2.Response(200, json={"data": {"id": "exp123"}})
        return httpx2.Response(404, json={"message": "not found"})

    def download(request: httpx2.Request) -> httpx2.Response:
        captured["fetch_headers"] = dict(request.headers)
        if str(request.url) == SHARE_URL:
            return httpx2.Response(200, content=PDF, headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="Rechnung 42.pdf"',
            })
        if request.url.path == "/share/redirect":
            return httpx2.Response(302, headers={"location": "http://169.254.169.254/"})
        return httpx2.Response(404)

    return (
        httpx2.AsyncClient(base_url="http://invoiceninja:80", auth=server.InvoiceNinjaTokenAuth(),
                          transport=httpx2.MockTransport(invoiceninja)),
        httpx2.AsyncClient(transport=httpx2.MockTransport(download)),
    )


def _call(args: dict, captured: dict):
    async def run():
        reset = server._incoming_auth.set("Bearer test-token")
        try:
            client, fetcher = _clients(captured)
            mcp = server.build_server(client=client, fetcher=fetcher)
            async with Client(mcp) as c:
                return await c.call_tool("attachDocumentFromUrl", args, raise_on_error=False)
        finally:
            server._incoming_auth.reset(reset)
    return asyncio.run(run())


@pytest.fixture
def allowed(monkeypatch):
    monkeypatch.setenv(server.FETCH_HOSTS_ENV, "paperless.example.com")


def test_tool_absent_without_allowed_hosts(monkeypatch):
    monkeypatch.delenv(server.FETCH_HOSTS_ENV, raising=False)
    names = {t.name for t in asyncio.run(server.build_server().list_tools())}
    assert "attachDocumentFromUrl" not in names


def test_downloads_and_uploads_with_spoofed_put(allowed):
    captured: dict = {}
    result = _call({"entity": "expenses", "id": "exp123", "url": SHARE_URL}, captured)
    assert result.is_error is False
    assert result.structured_content["filename"] == "Rechnung 42.pdf"
    assert result.structured_content["size"] == len(PDF)
    assert PDF in captured["body"]
    assert b'filename="Rechnung 42.pdf"' in captured["body"]
    assert b'name="_method"' in captured["body"] and b"PUT" in captured["body"]
    assert captured["token"] == "test-token"
    # The caller's InvoiceNinja token must never be sent to the download host.
    assert "x-api-token" not in captured["fetch_headers"]
    assert "authorization" not in captured["fetch_headers"]


def test_filename_override(allowed):
    captured: dict = {}
    result = _call({"entity": "expenses", "id": "exp123", "url": SHARE_URL,
                    "filename": "Hetzner.pdf"}, captured)
    assert result.structured_content["filename"] == "Hetzner.pdf"
    assert b'filename="Hetzner.pdf"' in captured["body"]


@pytest.mark.parametrize("url", [
    "https://evil.example.com/share/abc",
    "http://paperless.example.com/share/abc",
    "https://paperless.example.com.evil.com/x",
])
def test_rejects_urls_outside_allowlist(allowed, url):
    captured: dict = {}
    result = _call({"entity": "expenses", "id": "exp123", "url": url}, captured)
    assert result.is_error is True
    assert "fetch_headers" not in captured


def test_does_not_follow_redirects(allowed):
    captured: dict = {}
    result = _call({"entity": "expenses", "id": "exp123",
                    "url": "https://paperless.example.com/share/redirect"}, captured)
    assert result.is_error is True
    assert "body" not in captured


def test_rejects_unknown_entity_and_bad_id(allowed):
    captured: dict = {}
    assert _call({"entity": "tokens", "id": "exp123", "url": SHARE_URL}, captured).is_error
    assert _call({"entity": "expenses", "id": "../x", "url": SHARE_URL}, captured).is_error
    assert "fetch_headers" not in captured
