import asyncio

import pytest

import server

OAUTH_VARS = {"MCP_BASE_URL": "https://mcp.example", "MCP_OAUTH_SECRET": "s3cret-s3cret"}


@pytest.mark.parametrize(
    "mode, env, expected",
    [
        (None, {}, "token"),          # unset + no OAuth vars: safe upgrade path
        (None, OAUTH_VARS, "both"),   # unset + vars: default is both
        ("token", {}, "token"),
        ("oauth", OAUTH_VARS, "oauth"),
        ("BOTH", OAUTH_VARS, "both"),
    ],
)
def test_resolve_auth_mode(monkeypatch, mode, env, expected):
    for var in ("MCP_AUTH_MODE", *OAUTH_VARS):
        monkeypatch.delenv(var, raising=False)
    if mode:
        monkeypatch.setenv("MCP_AUTH_MODE", mode)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert server.resolve_auth_mode() == expected


@pytest.mark.parametrize("mode", ["oauth", "both", "bogus"])
def test_resolve_auth_mode_fails_loudly_when_explicit(monkeypatch, mode):
    for var in OAUTH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MCP_AUTH_MODE", mode)
    with pytest.raises(RuntimeError, match="MCP_"):
        server.resolve_auth_mode()


def test_plain_http_base_url_fails_at_startup(monkeypatch):
    monkeypatch.setenv("MCP_BASE_URL", "http://192.168.1.50:8081")
    with pytest.raises(RuntimeError, match="MCP_BASE_URL.*HTTPS"):
        server.validate_base_url()


def test_localhost_http_base_url_is_allowed(monkeypatch):
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8081/")
    assert server.validate_base_url() == "http://localhost:8081"


def test_encrypted_store_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "OAUTH_STORE_DIR", tmp_path)
    monkeypatch.setenv("MCP_OAUTH_SECRET", "s3cret-s3cret")
    store = server.build_oauth_store()

    async def go():
        await store.put("k", {"secret": "needle-value"}, collection="c")
        return await store.get("k", collection="c")

    assert asyncio.run(go()) == {"secret": "needle-value"}
    stored = b"".join(f.read_bytes() for f in tmp_path.rglob("*") if f.is_file())
    assert stored and b"needle-value" not in stored  # encrypted at rest


import json

import httpx
from key_value.aio.stores.memory import MemoryStore
from mcp.shared.auth import OAuthClientInformationFull

CLIENT = OAuthClientInformationFull(client_id="c1", redirect_uris=["http://localhost:9/cb"])


def fake_invoiceninja(calls):
    """A tiny InvoiceNinja: /login (password 'pw'), /tokens mint + delete."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/v1/login":
            if json.loads(request.content).get("password") == "pw":
                return httpx.Response(200, json={"data": [
                    {"token": {"token": "session-tok", "name": "User Token"}}
                ]})
            return httpx.Response(
                400, json={"message": "These credentials do not match our records"}
            )
        if path == "/api/v1/tokens" and request.method == "POST":
            name = json.loads(request.content)["name"]
            return httpx.Response(200, json={"data": {
                "id": "tid1", "token": "minted-tok", "name": name}})
        if path == "/api/v1/tokens/tid1" and request.method == "DELETE":
            return httpx.Response(200, json={"data": {}})
        return httpx.Response(404)

    return handler


def make_provider(passthrough=True, handler=None):
    """Provider on an in-memory store with a fake InvoiceNinja -> (provider, calls)."""
    calls = []
    api = httpx.AsyncClient(
        base_url="http://invoiceninja:80",
        headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
        transport=httpx.MockTransport(handler or fake_invoiceninja(calls)),
    )
    provider = server.InvoiceNinjaOAuthProvider(
        base_url="http://localhost", store=MemoryStore(), api=api, passthrough=passthrough
    )
    return provider, calls


def issue(p, resource=None):
    return asyncio.run(p._issue("c1", [], "minted-tok", "tid1", resource))


def test_issued_token_maps_to_api_token():
    p, _ = make_provider()
    tok = issue(p)
    found = asyncio.run(p.verify_token(tok.access_token))
    assert tok.access_token.startswith(server.OAUTH_TOKEN_PREFIX)
    assert found.claims["api_token"] == "minted-tok"


def test_both_mode_passes_unknown_bearer_through():
    p, _ = make_provider(passthrough=True)
    found = asyncio.run(p.verify_token("raw-api-token"))
    assert found.claims["api_token"] == "raw-api-token"


def test_oauth_mode_rejects_unknown_bearer():
    p, _ = make_provider(passthrough=False)
    assert asyncio.run(p.verify_token("raw-api-token")) is None


def test_stale_prefixed_token_is_rejected_even_in_both_mode():
    # An expired/revoked OAuth token must 401 (so the client refreshes), not be
    # forwarded to InvoiceNinja as if it were a raw API token.
    p, _ = make_provider(passthrough=True)
    assert asyncio.run(p.verify_token(server.OAUTH_TOKEN_PREFIX + "gone")) is None


def test_refresh_rotates_the_pair_and_keeps_the_minted_token():
    p, _ = make_provider()

    async def go():
        first = await p._issue("c1", [], "minted-tok", "tid1", None)
        rt = await p.load_refresh_token(CLIENT, first.refresh_token)
        second = await p.exchange_refresh_token(CLIENT, rt, [])
        return (
            await p.verify_token(first.access_token),
            await p.load_refresh_token(CLIENT, first.refresh_token),
            await p.verify_token(second.access_token),
        )

    old_access, old_refresh, new_access = asyncio.run(go())
    assert old_access is None and old_refresh is None
    assert new_access.claims["api_token"] == "minted-tok"


def test_revoke_drops_pair_and_deletes_minted_token():
    p, calls = make_provider()
    tok = issue(p)

    async def go():
        await p.revoke_token(await p.load_access_token(tok.access_token))
        return await p.load_refresh_token(CLIENT, tok.refresh_token)

    assert asyncio.run(go()) is None
    dele = [c for c in calls if c.method == "DELETE"]
    assert dele and dele[0].url.path == "/api/v1/tokens/tid1"
    assert dele[0].headers["X-API-TOKEN"] == "minted-tok"
    assert not any(c.url.path.endswith("/logout") for c in calls)


def test_revoke_survives_invoiceninja_being_down():
    def down(request):
        raise httpx.ConnectError("down")

    p, _ = make_provider(handler=down)
    tok = issue(p)

    async def go():
        await p.revoke_token(await p.load_access_token(tok.access_token))
        return await p.verify_token(tok.access_token)

    assert asyncio.run(go()) is None  # pair gone, no exception


@pytest.mark.parametrize(
    "resource, ok",
    [
        (None, True),                          # clients that send no resource
        ("http://localhost/mcp", True),
        ("http://localhost/mcp/", True),
        ("http://localhost", True),
        ("http://other.example/mcp", False),   # token meant for another server
    ],
)
def test_access_token_is_bound_to_this_resource(resource, ok):
    p, _ = make_provider()
    tok = issue(p, resource=resource)
    assert (asyncio.run(p.verify_token(tok.access_token)) is not None) is ok


def test_registered_client_round_trips():
    p, _ = make_provider()

    async def go():
        await p.register_client(CLIENT)
        return await p.get_client("c1")

    assert asyncio.run(go()).client_id == "c1"


def test_build_oauth_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "OAUTH_STORE_DIR", tmp_path)
    monkeypatch.setenv("MCP_BASE_URL", "https://mcp.example")
    monkeypatch.setenv("MCP_OAUTH_SECRET", "s3cret-s3cret")
    p = server.build_oauth_provider("both")
    assert p.passthrough is True
    assert str(p.base_url).rstrip("/") == "https://mcp.example"
