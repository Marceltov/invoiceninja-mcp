import asyncio
import base64

import httpx

import server


def _mock_client() -> httpx.AsyncClient:
    """An httpx client whose transport fakes InvoiceNinja's client-list response.

    base_url is the bare server root (no /api/v1 suffix): the spec's own path
    keys already include /api/v1 (e.g. "/api/v1/clients"), and httpx's
    base_url + relative-path concatenation would double it up if we appended
    /api/v1 here too. The handler asserts the exact path for this reason --
    an endswith("/clients") check would still match a doubled
    "/api/v1/api/v1/clients" and silently hide that bug.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/clients" and request.method == "GET":
            return httpx.Response(200, json={"data": [], "meta": {"pagination": {}}})
        return httpx.Response(404, json={"message": "not found"})

    return httpx.AsyncClient(
        base_url="http://invoiceninja:80",
        auth=server.InvoiceNinjaTokenAuth(),
        transport=httpx.MockTransport(handler),
    )


def _upload_capture_client(captured: dict) -> httpx.AsyncClient:
    """Client whose transport records the multipart upload request."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/clients/abc123/upload":
            captured["content_type"] = request.headers.get("content-type")
            captured["body"] = request.content
            return httpx.Response(200, json={"data": {"id": "abc123"}})
        return httpx.Response(404, json={"message": "not found"})

    return httpx.AsyncClient(
        base_url="http://invoiceninja:80",
        auth=server.InvoiceNinjaTokenAuth(),
        transport=httpx.MockTransport(handler),
    )


def test_build_server_needs_no_token_and_builds_tools():
    mcp = server.build_server()
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert len(tools) >= 300
    assert "storeClient" in names
    assert "showClient" in names


def test_auth_session_tools_are_excluded():
    # login/logout manage InvoiceNinja session tokens and must not be exposed
    # as MCP tools: an LLM authenticates via the Authorization header.
    mcp = server.build_server()
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "login" not in names
    assert "logout" not in names


def test_upload_client_replaces_generated_tool_exactly_once():
    mcp = server.build_server()
    names = [t.name for t in asyncio.run(mcp.list_tools())]
    assert names.count("uploadClient") == 1


def test_upload_client_sends_multipart_with_decoded_bytes():
    from fastmcp import Client

    captured: dict = {}

    async def run():
        reset = server._incoming_auth.set("Bearer test-token")
        try:
            mcp = server.build_server(client=_upload_capture_client(captured))
            async with Client(mcp) as c:
                return await c.call_tool("uploadClient", {
                    "id": "abc123",
                    "filename": "note.txt",
                    "content_base64": base64.b64encode(b"hello world").decode(),
                })
        finally:
            server._incoming_auth.reset(reset)

    result = asyncio.run(run())
    assert result.is_error is False
    assert captured["content_type"].startswith("multipart/form-data")
    assert b"hello world" in captured["body"]
    assert b"note.txt" in captured["body"]
    # InvoiceNinja registers this route as PUT, not the POST the OpenAPI spec
    # documents -- and PHP/Symfony don't parse multipart bodies on a raw PUT
    # request, so the request must be a POST carrying Laravel's method-
    # spoofing field to actually land as a PUT server-side.
    assert b'name="_method"' in captured["body"]
    assert b"PUT" in captured["body"]


def test_get_clients_works_through_generated_tool():
    from fastmcp import Client

    async def run():
        reset = server._incoming_auth.set("Bearer test-token")
        try:
            mcp = server.build_server(client=_mock_client())
            async with Client(mcp) as c:
                return await c.call_tool("getClients", {})
        finally:
            server._incoming_auth.reset(reset)

    result = asyncio.run(run())
    assert result.is_error is False


from pathlib import Path

import pytest

import server


def test_load_spec_missing_file_raises(tmp_path):
    with pytest.raises(RuntimeError, match="not found"):
        server.load_spec(tmp_path / "does-not-exist.yaml")


def test_load_spec_empty_file_raises(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    with pytest.raises(RuntimeError, match="empty"):
        server.load_spec(empty)


def test_load_spec_non_mapping_raises(tmp_path):
    bad = tmp_path / "list.yaml"
    bad.write_text("- just\n- a\n- list\n")
    with pytest.raises(RuntimeError, match="not a valid mapping"):
        server.load_spec(bad)


def test_build_error_server_exposes_startup_error_tool():
    import asyncio

    from fastmcp import Client

    mcp = server.build_error_server(RuntimeError("spec exploded"))

    async def run():
        async with Client(mcp) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}
            assert names == {"startup_error"}
            result = await c.call_tool("startup_error", {})
            return result

    result = asyncio.run(run())
    assert "spec exploded" in result.content[0].text
