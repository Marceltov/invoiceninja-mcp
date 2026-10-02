"""OAuth login end to end against the dev stack: register, authorize, log in with
the fixture credentials, call a tool with the OAuth token, then revoke."""

import asyncio
import base64
import hashlib
import secrets

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.live._client import MCP_URL

EMAIL = "admin@example.com"
PASSWORD = "invoiceninja-mcp-dev"  # committed dev fixture credential (see CLAUDE.md)
REDIRECT = "http://localhost:9/cb"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-03-26", "capabilities": {},
    "clientInfo": {"name": "t", "version": "0"}}}
ORIGIN = httpx.URL(MCP_URL).copy_with(path="/", query=None)


def _local(url: str) -> httpx.URL:
    """The server hands out absolute URLs built from MCP_BASE_URL; follow them on
    the stack we're actually testing."""
    u = httpx.URL(url)
    return ORIGIN.copy_with(path=u.path, query=u.query)


def test_oauth_login_tool_call_and_revoke():
    async def run():
        async with httpx.AsyncClient(follow_redirects=False, timeout=30) as http:
            reg = await http.post(str(ORIGIN.copy_with(path="/register")), json={
                "client_name": "live-oauth-test", "redirect_uris": [REDIRECT],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            })
            assert reg.status_code in (200, 201), reg.text
            client_id = reg.json()["client_id"]
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode()).digest()
            ).rstrip(b"=").decode()
            r = await http.get(str(ORIGIN.copy_with(path="/authorize")), params={
                "response_type": "code", "client_id": client_id,
                "redirect_uri": REDIRECT, "code_challenge": challenge,
                "code_challenge_method": "S256", "state": "s",
            })
            login_url = _local(r.headers["location"])
            r = await http.post(str(login_url.copy_with(query=None)), data={
                "id": login_url.params["id"], "email": EMAIL, "password": PASSWORD})
            assert r.status_code == 302, r.text
            code = httpx.URL(r.headers["location"]).params["code"]
            r = await http.post(str(ORIGIN.copy_with(path="/token")), data={
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": REDIRECT, "client_id": client_id,
                "code_verifier": verifier,
            })
            assert r.status_code == 200, r.text
            access = r.json()["access_token"]
            raw_headers = {"Authorization": f"Bearer {access}",
                           "Accept": "application/json, text/event-stream"}

            transport = StreamableHttpTransport(
                url=MCP_URL, headers={"Authorization": f"Bearer {access}"})
            async with Client(transport) as c:
                assert await c.call_tool("getClients", {}) is not None

            # Same raw request before and after revoke: 401 afterwards must mean
            # "token rejected", not "malformed request" or an outage.
            r = await http.post(MCP_URL, headers=raw_headers, json=INIT)
            assert r.status_code != 401, r.text
            assert r.status_code == 200, r.text

            r = await http.post(str(ORIGIN.copy_with(path="/revoke")), data={
                "token": access, "client_id": client_id,
                "client_secret": ""})  # the SDK's /revoke requires the field even for public clients
            assert r.status_code == 200

            r = await http.post(MCP_URL, headers=raw_headers, json=INIT)
            assert r.status_code == 401, f"revoked token: {r.status_code} {r.text}"

    asyncio.run(run())
