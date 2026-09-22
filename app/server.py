# SPDX-License-Identifier: AGPL-3.0-or-later
#
# invoiceninja-mcp — Standalone MCP server exposing the InvoiceNinja API over HTTP.
# Copyright (C) 2026 Marcel Bruckner
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.

import base64
from contextvars import ContextVar

import httpx

# All configuration comes from the environment so the server runs cleanly as a
# container sidecar with no command-line arguments.
SERVER_ENV = "INVOICENINJA_SERVER_URL"     # Base URL of the InvoiceNinja instance
SPEC_ENV = "INVOICENINJA_API_SPEC"         # Override path to the OpenAPI spec
MCP_HOST_ENV = "MCP_HOST"                  # Interface the MCP server binds to
MCP_PORT_ENV = "MCP_PORT"                  # Port the MCP server listens on
MCP_PATH_ENV = "MCP_PATH"                  # HTTP path the MCP endpoint is served at
MCP_ALLOWED_HOSTS_ENV = "MCP_ALLOWED_HOSTS"  # comma-separated Host allowlist

DEFAULT_SERVER_URL = "http://invoiceninja:80"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8081
DEFAULT_PATH = "/mcp"
HEALTH_PATH = "/health"

# Per-request holder for the incoming client Authorization header. Populated by
# TokenCaptureMiddleware and read by InvoiceNinjaTokenAuth when calling InvoiceNinja.
_incoming_auth: ContextVar[str | None] = ContextVar("incoming_auth", default=None)


class InvoiceNinjaTokenAuth(httpx.Auth):
    """Forward the client-supplied token to InvoiceNinja as X-API-TOKEN.

    The token arrives per-request in the `_incoming_auth` contextvar (set by
    TokenCaptureMiddleware) as a client-facing `Authorization` header value —
    the same client-facing convention trillium-mcp's ETAPI server uses.
    InvoiceNinja's API instead expects the raw token under a distinct
    `X-API-TOKEN` header, so we strip a leading 'Bearer ' if present and set
    it under that name. `X-Requested-With` is set unconditionally alongside
    it: InvoiceNinja's developer docs describe it as required security-minded
    header on API calls, and sending it costs nothing on requests where it
    turns out not to be enforced.
    """

    def auth_flow(self, request: httpx.Request):
        raw = _incoming_auth.get()
        if raw and raw[:7].lower() == "bearer ":
            raw = raw[7:].strip()
        if not raw:
            raise RuntimeError(
                "No client Authorization header available for the InvoiceNinja API call."
            )
        request.headers["X-API-TOKEN"] = raw
        request.headers["X-Requested-With"] = "XMLHttpRequest"
        yield request


class TokenCaptureMiddleware:
    """Pure-ASGI middleware that requires a client Authorization header on the
    MCP endpoint and stashes it for the outgoing InvoiceNinja call.

    The token IS the auth: a request without one is rejected with 401 before it
    reaches FastMCP; validity is enforced by InvoiceNinja on the actual API call.
    Implemented at the ASGI layer (not BaseHTTPMiddleware) so it does not buffer
    the streamable-HTTP response. The health check is always allowed.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            # Forward lifespan / websocket scopes untouched.
            await self.app(scope, receive, send)
            return
        if scope.get("path") == HEALTH_PATH:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        authorization = headers.get(b"authorization", b"").decode()
        if not authorization:
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": b'{"error":"missing Authorization header"}',
            })
            return
        token = _incoming_auth.set(authorization)
        try:
            await self.app(scope, receive, send)
        finally:
            _incoming_auth.reset(token)
