"""Shared helpers for the live integration tests (see conftest.py for the skip)."""

import asyncio
import os
from pathlib import Path

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

# app/tests/live/_client.py -> parents: [live, tests, app, <repo root>]
REPO_ROOT = Path(__file__).resolve().parents[3]
MCP_URL = os.environ.get("INVOICENINJA_MCP_URL", "http://localhost:8081/mcp")
HEALTH_URL = str(httpx.URL(MCP_URL).copy_with(path="/health", query=None))


def _token() -> str | None:
    tok = os.environ.get("INVOICENINJA_API_TOKEN")
    if tok:
        return tok.strip()
    token_file = REPO_ROOT / "invoiceninja.token"
    return token_file.read_text().strip() if token_file.exists() else None


TOKEN = _token()

_reachable: bool | None = None


def stack_reachable() -> bool:
    """True if the MCP /health endpoint answers 200 and we have a token.
    Cached so the network probe runs once per session."""
    global _reachable
    if _reachable is None:
        try:
            _reachable = bool(TOKEN) and (
                httpx.get(HEALTH_URL, timeout=2.0).status_code == 200
            )
        except httpx.HTTPError:
            _reachable = False
    return _reachable


def client() -> Client:
    return Client(StreamableHttpTransport(MCP_URL, headers={"Authorization": TOKEN}))


def run_async(coro):
    return asyncio.run(coro)


async def call(tool: str, args: dict | None = None):
    async with client() as c:
        return await c.call_tool(tool, args or {})
