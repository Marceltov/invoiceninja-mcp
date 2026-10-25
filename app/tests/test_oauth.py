"""OAuth through mcp-login-oauth against a fake InvoiceNinja. The library's own
tests cover the OAuth machinery; these cover how this server plugs into it: the
email/password/2FA login, the named token minted per client, deleting it (never
/logout) on revoke, both mode and the /upload route."""

import asyncio
import base64
import hashlib
import json
import secrets

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.testclient import TestClient

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


def test_build_oauth_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "OAUTH_STORE_DIR", tmp_path)
    for k, v in OAUTH_VARS.items():
        monkeypatch.setenv(k, v)
    both, oauth = server.build_oauth_provider("both"), server.build_oauth_provider("oauth")
    assert both.verify_static is not None and oauth.verify_static is None
    assert str(both.base_url).rstrip("/") == "https://mcp.example"
    assert both.token_prefix == server.OAUTH_TOKEN_PREFIX


CLIENT = OAuthClientInformationFull(client_id="c1", redirect_uris=["http://localhost:9/cb"])
REDIRECT = "http://localhost:9/cb"


@pytest.fixture(autouse=True)
def local_base_url(monkeypatch):
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost")


def fake_invoiceninja(calls):
    """A tiny InvoiceNinja: /login (password 'pw'), /tokens mint + delete."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/v1/login":
            if json.loads(request.content).get("password") == "pw":
                return httpx2.Response(200, json={"data": [
                    {"token": {"token": "session-tok", "name": "User Token"}}
                ]})
            return httpx2.Response(
                400, json={"message": "These credentials do not match our records"}
            )
        if path == "/api/v1/tokens" and request.method == "POST":
            name = json.loads(request.content)["name"]
            return httpx2.Response(200, json={"data": {
                "id": "tid1", "token": "minted-tok", "name": name}})
        if path == "/api/v1/tokens/tid1" and request.method == "DELETE":
            return httpx2.Response(200, json={"data": {}})
        return httpx2.Response(404)

    return handler


def api_client(handler) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url="http://invoiceninja:80",
        headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
        transport=httpx2.MockTransport(handler),
    )


def make_provider(mode="both", handler=None):
    """Provider on an in-memory store with a fake InvoiceNinja -> (provider, calls)."""
    calls = []
    provider = server.build_oauth_provider(
        mode, api=api_client(handler or fake_invoiceninja(calls)), store=MemoryStore())
    return provider, calls


def test_both_mode_passes_unknown_bearer_through():
    p, _ = make_provider("both")
    found = asyncio.run(p.verify_token("raw-api-token"))
    assert found.claims["grant"]["api_token"] == "raw-api-token"


def test_oauth_mode_rejects_unknown_bearer():
    p, _ = make_provider("oauth")
    assert asyncio.run(p.verify_token("raw-api-token")) is None


def test_stale_prefixed_token_is_rejected_even_in_both_mode():
    # An expired/revoked OAuth token must 401 (so the client refreshes), not be
    # forwarded to InvoiceNinja as if it were a raw API token.
    p, _ = make_provider("both")
    assert asyncio.run(p.verify_token(server.OAUTH_TOKEN_PREFIX + "gone")) is None


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
            redirect_uri=redirect or REDIRECT, redirect_uri_provided_explicitly=True,
        ))
        return httpx2.URL(url).params["id"]

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
    assert "localhost:9" in page.text and "your InvoiceNinja" in page.text
    assert 'name="email"' in page.text and 'name="one_time_password"' in page.text


def test_login_mints_named_token_and_redirects_with_code_state_and_iss():
    p, calls = make_provider()
    pid = begin_login(p)
    r = login_client(p).post("/login", data={"id": pid, "email": "a@b.c", "password": "pw"})
    assert r.status_code == 302
    back = httpx2.URL(r.headers["location"])
    assert back.params["state"] == "s1"
    meta = login_client(p).get("/.well-known/oauth-authorization-server").json()
    assert back.params["iss"] == meta["issuer"]
    assert meta["authorization_response_iss_parameter_supported"] is True
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
        httpx2.Response(200, json={"data": []}),                 # user in no company
        httpx2.Response(502, text="<html>bad gateway</html>"),   # not JSON
        httpx2.Response(200, text="not json"),
        httpx2.Response(400, json={"message": {"email": ["bad"]}}),  # non-string message
        httpx2.Response(400, json={"message": ["x"]}),
    ],
)
def test_unexpected_login_answers_show_an_error_not_a_500(response):
    p, _ = make_provider(handler=lambda request: response)
    pid = begin_login(p)
    r = login_client(p).post("/login", data={"id": pid, "email": "a@b.c", "password": "pw"})
    assert r.status_code == 401
    assert "InvoiceNinja" in r.text


def test_unreachable_invoiceninja_is_502_and_not_a_failed_attempt():
    def down(request):
        raise httpx2.ConnectError("down")

    p, _ = make_provider(handler=down)
    pid = begin_login(p)
    r = login_client(p).post("/login", data={"id": pid, "email": "a@b.c", "password": "pw"})
    assert r.status_code == 502 and "Could not reach InvoiceNinja." in r.text


def test_minting_failure_shows_an_error_and_issues_no_code():
    def handler(request):
        if request.url.path == "/api/v1/login":
            return httpx2.Response(200, json={"data": [{"token": {"token": "session-tok"}}]})
        return httpx2.Response(403, json={"message": "Forbidden"})

    p, _ = make_provider(handler=handler)
    pid = begin_login(p)
    r = login_client(p).post("/login", data={"id": pid, "email": "a@b.c", "password": "pw"})
    assert r.status_code == 401 and "Forbidden" in r.text and "location" not in r.headers


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


def make_app(mode, seen, handler=None):
    """Full in-process stack: provider + generated tools + wrap_app, one fake
    InvoiceNinja that records the X-API-TOKEN of GET /api/v1/clients and of the
    upload endpoint. Returns (inner, app, calls)."""
    calls = []
    base_handler = handler or fake_invoiceninja(calls)

    def handle(request):
        if request.url.path == "/api/v1/clients":
            seen["token"] = request.headers.get("X-API-TOKEN")
            return httpx2.Response(200, json={"data": [], "meta": {}})
        if request.url.path == "/api/v1/clients/abc/upload":
            seen["upload_token"] = request.headers.get("X-API-TOKEN")
            return httpx2.Response(200, json={"data": {"id": "abc"}})
        return base_handler(request)

    provider = server.build_oauth_provider(mode, api=api_client(handle), store=MemoryStore())
    mcp = server.build_server(
        client=httpx2.AsyncClient(base_url="http://invoiceninja:80",
                                  auth=server.InvoiceNinjaTokenAuth(),
                                  transport=httpx2.MockTransport(handle)),
        auth=provider,
    )
    inner = mcp.http_app(path=server.DEFAULT_PATH)
    return inner, server.wrap_app(inner, mode, provider), calls


def factory_for(app):
    def factory(**kw):
        kw.pop("transport", None)
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://localhost", **kw
        )
    return factory


async def oauth_tokens(http, resource="http://localhost/mcp") -> dict:
    """register -> authorize -> /login -> /token; the token response plus client_id."""
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
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
              "code_challenge": challenge, "code_challenge_method": "S256", "state": "s1"}
    r = await http.get("/authorize", params={**params, **({"resource": resource} if resource else {})})
    pending = httpx2.URL(r.headers["location"]).params["id"]
    r = await http.post("/login", data={"id": pending, "email": "a@b.c", "password": "pw"})
    back = httpx2.URL(r.headers["location"])
    assert back.params["state"] == "s1" and back.params["iss"] == "http://localhost/"
    r = await http.post("/token", data={
        "grant_type": "authorization_code", "code": back.params["code"],
        "redirect_uri": REDIRECT, "client_id": client_id, "code_verifier": verifier,
        **({"resource": resource} if resource else {}),
    })
    assert r.status_code == 200, r.text
    return {**r.json(), "client_id": client_id}


async def oauth_access_token(http) -> str:
    return (await oauth_tokens(http))["access_token"]


def run(inner, scenario):
    async def go():
        async with inner.router.lifespan_context(inner):
            return await scenario()
    return asyncio.run(go())


def test_full_oauth_flow_forwards_minted_token():
    """register -> authorize -> /login -> token -> MCP tool call, in-process."""
    seen = {}
    inner, app, _ = make_app("oauth", seen)
    factory = factory_for(app)

    async def scenario():
        async with factory() as http:
            assert (await http.get("/health")).status_code == 200
            r = await http.post(server.DEFAULT_PATH, json={})
            assert r.status_code == 401
            assert "resource_metadata" in r.headers["www-authenticate"]
            access = await oauth_access_token(http)
        transport = StreamableHttpTransport(
            url="http://localhost/mcp", headers={"Authorization": f"Bearer {access}"},
            httpx_client_factory=factory,
        )
        async with Client(transport) as c:
            return await c.call_tool("getClients", {})

    run(inner, scenario)
    assert seen["token"] == "minted-tok"


def test_refresh_rotates_the_pair_and_keeps_the_minted_token():
    seen = {}
    inner, app, calls = make_app("oauth", seen)
    factory = factory_for(app)

    async def scenario():
        async with factory() as http:
            tokens = await oauth_tokens(http)
            r = await http.post("/token", data={"grant_type": "refresh_token", "client_id": tokens["client_id"],
                                                "refresh_token": tokens["refresh_token"]})
            assert r.status_code == 200, r.text
            old = await http.post(server.DEFAULT_PATH, json={},
                                  headers={"Authorization": f"Bearer {tokens['access_token']}"})
            assert old.status_code == 401
        transport = StreamableHttpTransport(
            url="http://localhost/mcp", headers={"Authorization": f"Bearer {r.json()['access_token']}"},
            httpx_client_factory=factory,
        )
        async with Client(transport) as c:
            await c.call_tool("getClients", {})

    run(inner, scenario)
    assert seen["token"] == "minted-tok"
    assert len([c for c in calls if c.url.path == "/api/v1/tokens"]) == 1  # minted once


def test_revoke_deletes_the_minted_token_and_never_logs_out():
    """claude.ai is a public client: revoking must work without a client secret,
    delete the named token, and leave the user's session token alone."""
    inner, app, calls = make_app("oauth", {})

    async def scenario():
        async with factory_for(app)() as http:
            tokens = await oauth_tokens(http)
            r = await http.post("/revoke", data={"token": tokens["access_token"],
                                                 "client_id": tokens["client_id"]})
            assert r.status_code == 200
            after = await http.post(server.DEFAULT_PATH, json={},
                                    headers={"Authorization": f"Bearer {tokens['access_token']}"})
            return after.status_code

    assert run(inner, scenario) == 401
    dele = [c for c in calls if c.method == "DELETE"]
    assert len(dele) == 1 and dele[0].url.path == "/api/v1/tokens/tid1"
    assert dele[0].headers["X-API-TOKEN"] == "minted-tok"
    assert not any(c.url.path.endswith("/logout") for c in calls)


def test_revoke_survives_invoiceninja_being_down():
    calls = []
    healthy = fake_invoiceninja(calls)

    def handler(request):
        if request.method == "DELETE":
            raise httpx2.ConnectError("down")
        return healthy(request)

    inner, app, _ = make_app("oauth", {}, handler=handler)

    async def scenario():
        async with factory_for(app)() as http:
            tokens = await oauth_tokens(http)
            r = await http.post("/revoke", data={"token": tokens["refresh_token"],
                                                 "client_id": tokens["client_id"]})
            after = await http.post(server.DEFAULT_PATH, json={},
                                    headers={"Authorization": f"Bearer {tokens['access_token']}"})
            return r.status_code, after.status_code

    assert run(inner, scenario) == (200, 401)  # pair gone, no error


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
    inner, app, _ = make_app("oauth", {})

    async def scenario():
        async with factory_for(app)() as http:
            tokens = await oauth_tokens(http, resource=resource)
            r = await http.post(server.DEFAULT_PATH, json={},
                                headers={"Authorization": f"Bearer {tokens['access_token']}"})
            refresh = await http.post("/token", data={"grant_type": "refresh_token",
                                                      "client_id": tokens["client_id"],
                                                      "refresh_token": tokens["refresh_token"]})
            return r.status_code, refresh.status_code

    mcp_status, refresh_status = run(inner, scenario)
    assert (mcp_status != 401) is ok
    assert (refresh_status == 200) is ok


def test_upload_route_forwards_the_oauth_token():
    """The plain-HTTP /upload/{entity}/{id} route shares the client, so it must
    carry the OAuth-minted token too -- and refuse a request with no bearer."""
    seen = {}
    inner, app, _ = make_app("oauth", seen)

    async def scenario():
        async with factory_for(app)() as http:
            files = {"documents": ("a.txt", b"hello", "text/plain")}
            anon = await http.post("/upload/clients/abc", files=files)
            access = await oauth_access_token(http)
            ok = await http.post("/upload/clients/abc", files=files,
                                 headers={"Authorization": f"Bearer {access}"})
            return anon.status_code, ok.status_code

    assert run(inner, scenario) == (401, 200)
    assert seen["upload_token"] == "minted-tok"


def test_both_mode_accepts_raw_header_without_bearer():
    """Existing clients send `Authorization: <api token>` (no Bearer); in both
    mode that must still reach InvoiceNinja, as it does in token mode."""
    seen = {}
    inner, app, _ = make_app("both", seen)

    async def scenario():
        transport = StreamableHttpTransport(
            url="http://localhost/mcp", headers={"Authorization": "raw-api-token"},
            httpx_client_factory=factory_for(app),
        )
        async with Client(transport) as c:
            await c.call_tool("getClients", {})

    run(inner, scenario)
    assert seen["token"] == "raw-api-token"


def test_metadata_advertises_iss_parameter_support():
    inner, app, _ = make_app("oauth", {})

    async def scenario():
        async with factory_for(app)() as http:
            return (await http.get("/.well-known/oauth-authorization-server")).json()

    meta = run(inner, scenario)
    assert meta["authorization_response_iss_parameter_supported"] is True
    assert meta["issuer"].rstrip("/") == "http://localhost"
    assert "registration_endpoint" in meta


def test_both_mode_raw_header_upload_reaches_upstream():
    seen = {}
    inner, app, _ = make_app("both", seen)

    async def scenario():
        async with factory_for(app)() as http:
            r = await http.post("/upload/clients/abc",
                                files={"documents": ("a.txt", b"hi", "text/plain")},
                                headers={"Authorization": "raw-api-token"})
            return r.status_code

    assert run(inner, scenario) == 200
    assert seen["upload_token"] == "raw-api-token"
