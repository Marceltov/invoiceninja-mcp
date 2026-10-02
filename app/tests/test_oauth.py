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
from pydantic import AnyUrl
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


from mcp.server.auth.provider import AuthorizationParams
from starlette.applications import Starlette
from starlette.testclient import TestClient


def begin_login(p, client_name="itest", redirect=None):
    """Register CLIENT and start an authorize -> returns the pending login id."""
    update = {"client_name": client_name}
    if redirect:
        update["redirect_uris"] = [AnyUrl(redirect)]
    client = CLIENT.model_copy(update=update)

    async def go():
        await p.register_client(client)
        url = await p.authorize(client, AuthorizationParams(
            state="s1", scopes=[], code_challenge="x" * 43,
            redirect_uri=redirect or "http://localhost:9/cb", redirect_uri_provided_explicitly=True,
        ))
        return httpx.URL(url).params["id"]

    return asyncio.run(go())


def login_client(p):
    return TestClient(Starlette(routes=p.get_routes(server.DEFAULT_PATH)),
                      follow_redirects=False)


def test_login_page_shows_real_redirect_host_not_userinfo():
    p, _ = make_provider()
    pid = begin_login(p, redirect="https://claude.ai@evil.example:8443/cb")
    page = login_client(p).get("/login", params={"id": pid})
    assert "evil.example:8443" in page.text and "claude.ai@" not in page.text


def test_login_page_shows_client_and_redirect_host_escaped():
    p, _ = make_provider()
    pid = begin_login(p, client_name="<b>evil</b>")
    page = login_client(p).get("/login", params={"id": pid})
    assert page.status_code == 200
    assert "&lt;b&gt;evil&lt;/b&gt;" in page.text and "<b>evil</b>" not in page.text
    assert "localhost:9" in page.text
    assert 'name="email"' in page.text and 'name="one_time_password"' in page.text


def test_login_mints_named_token_and_redirects_with_code_state_and_iss():
    p, calls = make_provider()
    pid = begin_login(p)
    r = login_client(p).post("/login", data={"id": pid, "email": "a@b.c", "password": "pw"})
    assert r.status_code == 302
    back = httpx.URL(r.headers["location"])
    assert back.params["state"] == "s1"
    assert back.params["iss"] == "http://localhost"
    assert back.params["code"].startswith(server.OAUTH_TOKEN_PREFIX)
    mint = [c for c in calls if c.url.path == "/api/v1/tokens"][0]
    assert mint.headers["X-API-TOKEN"] == "session-tok"
    assert json.loads(mint.content)["name"] == "MCP: itest"
    assert not any(c.url.path.endswith("/logout") for c in calls)


def test_token_name_is_truncated_for_long_client_names():
    p, calls = make_provider()
    pid = begin_login(p, client_name="x" * 500)
    login_client(p).post("/login", data={"id": pid, "email": "a@b.c", "password": "pw"})
    mint = [c for c in calls if c.url.path == "/api/v1/tokens"][0]
    assert len(json.loads(mint.content)["name"]) <= 100


def test_wrong_password_rerenders_and_pending_login_survives():
    p, calls = make_provider()
    pid = begin_login(p)
    c = login_client(p)
    bad = c.post("/login", data={"id": pid, "email": "a@b.c", "password": "nope"})
    assert bad.status_code == 401
    assert "do not match our records" in bad.text
    assert not any(x.url.path == "/api/v1/tokens" for x in calls)
    ok = c.post("/login", data={"id": pid, "email": "a@b.c", "password": "pw"})
    assert ok.status_code == 302


def test_one_time_password_is_forwarded_only_when_given():
    p, calls = make_provider()
    pid = begin_login(p)
    login_client(p).post("/login", data={
        "id": pid, "email": "a@b.c", "password": "pw", "one_time_password": "123456"})
    body = json.loads([c for c in calls if c.url.path == "/api/v1/login"][0].content)
    assert body["one_time_password"] == "123456"

    p2, calls2 = make_provider()
    pid2 = begin_login(p2)
    login_client(p2).post("/login", data={"id": pid2, "email": "a@b.c", "password": "pw"})
    body2 = json.loads([c for c in calls2 if c.url.path == "/api/v1/login"][0].content)
    assert "one_time_password" not in body2


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"data": []}),                 # user in no company
        httpx.Response(502, text="<html>bad gateway</html>"),   # not JSON
        httpx.Response(200, text="not json"),
        httpx.Response(400, json={"message": {"email": ["bad"]}}),  # non-string message
        httpx.Response(400, json={"message": ["x"]}),
    ],
)
def test_unexpected_login_answers_show_an_error_not_a_500(response):
    p, _ = make_provider(handler=lambda request: response)
    pid = begin_login(p)
    r = login_client(p).post("/login", data={"id": pid, "email": "a@b.c", "password": "pw"})
    assert r.status_code == 401
    assert "InvoiceNinja" in r.text


def test_minting_failure_shows_an_error_and_issues_no_code():
    def handler(request):
        if request.url.path == "/api/v1/login":
            return httpx.Response(200, json={"data": [{"token": {"token": "session-tok"}}]})
        return httpx.Response(403, json={"message": "Forbidden"})

    p, _ = make_provider(handler=handler)
    pid = begin_login(p)
    r = login_client(p).post("/login", data={"id": pid, "email": "a@b.c", "password": "pw"})
    assert r.status_code == 401 and "Forbidden" in r.text


def test_expired_login_link_is_rejected():
    p, _ = make_provider()
    r = login_client(p).get("/login", params={"id": "nope"})
    assert r.status_code == 400
    assert "expired" in r.text.lower()


def test_replayed_login_says_completed_and_mints_nothing():
    p, calls = make_provider()
    pid = begin_login(p)
    c = login_client(p)
    assert c.post("/login", data={"id": pid, "email": "a@b.c", "password": "pw"}).status_code == 302
    mints_before = len([x for x in calls if x.url.path == "/api/v1/tokens"])
    again = c.post("/login", data={"id": pid, "email": "a@b.c", "password": "pw"})
    assert again.status_code == 200 and "already" in again.text.lower()
    assert len([x for x in calls if x.url.path == "/api/v1/tokens"]) == mints_before


import base64
import hashlib
import secrets

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

REDIRECT = "http://localhost:9/cb"


def make_app(passthrough, seen):
    """Full in-process stack: provider + generated tools + wrap_app, one fake
    InvoiceNinja that records the X-API-TOKEN of GET /api/v1/clients and of the
    upload endpoint."""
    calls = []
    base_handler = fake_invoiceninja(calls)

    def handler(request):
        if request.url.path == "/api/v1/clients":
            seen["token"] = request.headers.get("X-API-TOKEN")
            return httpx.Response(200, json={"data": [], "meta": {}})
        if request.url.path == "/api/v1/clients/abc/upload":
            seen["upload_token"] = request.headers.get("X-API-TOKEN")
            return httpx.Response(200, json={"data": {"id": "abc"}})
        return base_handler(request)

    mock = httpx.MockTransport(handler)
    provider = server.InvoiceNinjaOAuthProvider(
        base_url="http://localhost", store=MemoryStore(), passthrough=passthrough,
        api=httpx.AsyncClient(base_url="http://invoiceninja:80", transport=mock,
                              headers={"X-Requested-With": "XMLHttpRequest"}),
    )
    mcp = server.build_server(
        client=httpx.AsyncClient(base_url="http://invoiceninja:80",
                                 auth=server.InvoiceNinjaTokenAuth(), transport=mock),
        auth=provider,
    )
    inner = mcp.http_app(path=server.DEFAULT_PATH)
    mode = "both" if passthrough else "oauth"
    return inner, server.wrap_app(inner, mode, provider)


def factory_for(app):
    def factory(**kw):
        kw.pop("transport", None)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost", **kw
        )
    return factory


async def oauth_access_token(http) -> str:
    """register -> authorize -> /login -> /token; returns the access token."""
    r = await http.post("/register", json={
        "client_name": "itest", "redirect_uris": [REDIRECT],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    })
    client_id = r.json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    r = await http.get("/authorize", params={
        "response_type": "code", "client_id": client_id,
        "redirect_uri": REDIRECT, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "s1",
        "resource": "http://localhost/mcp",
    })
    pending = httpx.URL(r.headers["location"]).params["id"]
    r = await http.post("/login", data={"id": pending, "email": "a@b.c", "password": "pw"})
    back = httpx.URL(r.headers["location"])
    assert back.params["state"] == "s1" and back.params["iss"] == "http://localhost"
    r = await http.post("/token", data={
        "grant_type": "authorization_code", "code": back.params["code"],
        "redirect_uri": REDIRECT, "client_id": client_id,
        "code_verifier": verifier, "resource": "http://localhost/mcp",
    })
    return r.json()["access_token"]


def test_full_oauth_flow_forwards_minted_token():
    """register -> authorize -> /login -> token -> MCP tool call, in-process."""
    seen = {}
    inner, app = make_app(False, seen)
    factory = factory_for(app)

    async def run():
        async with inner.router.lifespan_context(inner):
            async with factory() as http:
                assert (await http.get("/health")).status_code == 200
                r = await http.post(server.DEFAULT_PATH, json={})
                assert r.status_code == 401
                assert "resource_metadata" in r.headers["www-authenticate"]
                access = await oauth_access_token(http)

            transport = StreamableHttpTransport(
                url="http://localhost/mcp",
                headers={"Authorization": f"Bearer {access}"},
                httpx_client_factory=factory,
            )
            async with Client(transport) as c:
                return await c.call_tool("getClients", {})

    asyncio.run(run())
    assert seen["token"] == "minted-tok"


def test_upload_route_forwards_the_oauth_token():
    """The plain-HTTP /upload/{entity}/{id} route shares the client, so it must
    carry the OAuth-minted token too -- and refuse a request with no bearer."""
    seen = {}
    inner, app = make_app(False, seen)

    async def run():
        async with inner.router.lifespan_context(inner):
            async with factory_for(app)() as http:
                files = {"documents": ("a.txt", b"hello", "text/plain")}
                anon = await http.post("/upload/clients/abc", files=files)
                access = await oauth_access_token(http)
                ok = await http.post(
                    "/upload/clients/abc", files=files,
                    headers={"Authorization": f"Bearer {access}"},
                )
                return anon.status_code, ok.status_code

    anon_status, ok_status = asyncio.run(run())
    assert anon_status == 401
    assert ok_status == 200
    assert seen["upload_token"] == "minted-tok"


def test_both_mode_accepts_raw_header_without_bearer():
    """Existing clients send `Authorization: <api token>` (no Bearer); in both
    mode that must still reach InvoiceNinja, as it does in token mode."""
    seen = {}
    inner, app = make_app(True, seen)
    factory = factory_for(app)

    async def run():
        async with inner.router.lifespan_context(inner):
            transport = StreamableHttpTransport(
                url="http://localhost/mcp",
                headers={"Authorization": "raw-api-token"},
                httpx_client_factory=factory,
            )
            async with Client(transport) as c:
                await c.call_tool("getClients", {})

    asyncio.run(run())
    assert seen["token"] == "raw-api-token"


def test_metadata_advertises_iss_parameter_support():
    inner, app = make_app(False, {})

    async def run():
        async with inner.router.lifespan_context(inner):
            async with factory_for(app)() as http:
                r = await http.get("/.well-known/oauth-authorization-server")
                return r.json()

    meta = asyncio.run(run())
    assert meta["authorization_response_iss_parameter_supported"] is True
    assert meta["issuer"].rstrip("/") == "http://localhost"
    assert "registration_endpoint" in meta  # untouched fields survive the rewrite


def test_both_mode_raw_header_upload_reaches_upstream():
    seen = {}
    inner, app = make_app(True, seen)

    async def run():
        async with inner.router.lifespan_context(inner):
            async with factory_for(app)() as http:
                r = await http.post(
                    "/upload/clients/abc",
                    files={"documents": ("a.txt", b"hi", "text/plain")},
                    headers={"Authorization": "raw-api-token"},
                )
                return r.status_code

    assert asyncio.run(run()) == 200
    assert seen["upload_token"] == "raw-api-token"
