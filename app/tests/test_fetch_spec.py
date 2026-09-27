import httpx

import server

_SPEC = "openapi: 3.0.1\ninfo: {title: t, version: '1'}\npaths: {}\n"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetches_upstream_spec_for_instance_version():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/api/v1/ping":
            # InvoiceNinja sends the version header even on a 403 (no token).
            return httpx.Response(403, headers={"X-APP-VERSION": "5.13.43"})
        return httpx.Response(200, text=_SPEC)

    spec = server.fetch_instance_spec("http://invoiceninja:80", _client(handler), retry_delay=0)
    assert spec["openapi"] == "3.0.1"
    assert seen[1] == server.UPSTREAM_SPEC_URL.format(version="5.13.43")


def test_returns_none_without_version_header():
    spec = server.fetch_instance_spec(
        "http://x", _client(lambda r: httpx.Response(200)), retry_delay=0
    )
    assert spec is None


def test_returns_none_when_upstream_has_no_spec_for_version():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/ping":
            return httpx.Response(403, headers={"X-APP-VERSION": "9.9.9"})
        return httpx.Response(404)

    assert server.fetch_instance_spec("http://x", _client(handler), retry_delay=0) is None


def test_retries_while_instance_is_not_up_yet():
    calls = {"ping": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/ping":
            calls["ping"] += 1
            if calls["ping"] < 3:
                raise httpx.ConnectError("not up yet")
            return httpx.Response(403, headers={"X-APP-VERSION": "5.13.43"})
        return httpx.Response(200, text=_SPEC)

    assert server.fetch_instance_spec("http://x", _client(handler), retry_delay=0) is not None
    assert calls["ping"] == 3
