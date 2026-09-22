from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import server


def _wrapped_app():
    async def echo(request):
        return PlainTextResponse(server._incoming_auth.get() or "")

    async def health(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[
        Route(server.DEFAULT_PATH, echo, methods=["GET", "POST"]),
        Route(server.HEALTH_PATH, health, methods=["GET"]),
    ])
    return server.TokenCaptureMiddleware(app)


def test_missing_auth_on_mcp_returns_401():
    client = TestClient(_wrapped_app())
    r = client.post(server.DEFAULT_PATH)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_health_open_without_auth():
    client = TestClient(_wrapped_app())
    r = client.get(server.HEALTH_PATH)
    assert r.status_code == 200
    assert r.text == "ok"


def test_auth_header_captured_into_contextvar():
    client = TestClient(_wrapped_app())
    r = client.post(server.DEFAULT_PATH, headers={"Authorization": "Bearer abc123"})
    assert r.status_code == 200
    assert r.text == "Bearer abc123"


def _host_protected_app():
    """Real FastMCP http_app with host protection on, wrapped the same way
    serve() wraps it. Unlike _wrapped_app() above (a hand-rolled Starlette
    fixture), this exercises FastMCP's actual HostOriginGuardMiddleware --
    that's the whole point: a hand-rolled fixture can't catch a bug in how
    we invoke FastMCP's real middleware (see MCP_ALLOWED_HOSTS regression
    below).
    """
    mcp = server.build_server()
    inner = mcp.http_app(
        path=server.DEFAULT_PATH,
        allowed_hosts=["allowed.example.com"],
        host_origin_protection=True,
    )
    return server.TokenCaptureMiddleware(inner)


def test_mismatched_host_is_rejected_on_mcp_path():
    # Proves MCP_ALLOWED_HOSTS actually gates the MCP endpoint by Host header
    # (host_origin_protection=True is required for allowed_hosts to do
    # anything at all -- passing allowed_hosts alone is a silent no-op).
    with TestClient(_host_protected_app()) as client:
        r = client.post(
            server.DEFAULT_PATH,
            headers={"Host": "evil.example.com", "Authorization": "Bearer abc123"},
        )
    assert r.status_code == 421


def test_health_open_even_with_mismatched_host():
    # /health must stay reachable with zero auth regardless of Host header,
    # even when MCP_ALLOWED_HOSTS (and thus HostOriginGuardMiddleware) is
    # active -- TokenCaptureMiddleware answers /health directly, without
    # ever delegating into the inner app that middleware guards.
    with TestClient(_host_protected_app()) as client:
        r = client.get(server.HEALTH_PATH, headers={"Host": "evil.example.com"})
    assert r.status_code == 200
    assert r.text == "ok"
