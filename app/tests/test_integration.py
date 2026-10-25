import asyncio

import httpx2
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

import server


def test_full_chain_forwards_token_as_x_api_token():
    """Client sends `Authorization: Bearer <token>`; TokenCaptureMiddleware
    captures it and InvoiceNinjaTokenAuth forwards the raw token (no Bearer
    prefix) as X-API-TOKEN to InvoiceNinja, driven through the real ASGI app
    end-to-end."""
    captured = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["x_api_token"] = request.headers.get("X-API-TOKEN")
        captured["authorization"] = request.headers.get("Authorization")
        # Exact path, not endswith: the spec's own path keys already include
        # /api/v1, so base_url below is the bare root (see build_server).
        if request.url.path == "/api/v1/clients":
            return httpx2.Response(200, json={"data": [], "meta": {"pagination": {}}})
        return httpx2.Response(404, json={"message": "not found"})

    backend = httpx2.AsyncClient(
        base_url="http://invoiceninja:80",
        auth=server.InvoiceNinjaTokenAuth(),
        transport=httpx2.MockTransport(handler),
    )
    mcp = server.build_server(client=backend)
    inner = mcp.http_app(path=server.DEFAULT_PATH)
    wrapped = server.TokenCaptureMiddleware(inner)

    def factory(**kw):
        kw.pop("transport", None)
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=wrapped),
            base_url="http://test", **kw,
        )

    transport = StreamableHttpTransport(
        url="http://test/mcp",
        headers={"Authorization": "Bearer secret-ninja-token"},
        httpx_client_factory=factory,
    )

    async def run():
        async with inner.router.lifespan_context(inner):
            async with Client(transport) as client:
                return await client.call_tool("getClients", {})

    result = asyncio.run(run())
    assert result.is_error is False
    assert captured["x_api_token"] == "secret-ninja-token"  # Bearer stripped
    assert captured["authorization"] is None  # not sent under this name
