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
