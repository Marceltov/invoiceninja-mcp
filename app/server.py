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
import os
import sys
import traceback
from contextvars import ContextVar
from pathlib import Path

import httpx
import uvicorn
import yaml
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap
from starlette.requests import Request
from starlette.responses import PlainTextResponse

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
            # Answered directly, without delegating into `self.app` (inner):
            # when MCP_ALLOWED_HOSTS is set, inner's HostOriginGuardMiddleware
            # would otherwise gate /health by Host header too, breaking the
            # "always reachable" contract this middleware promises.
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            })
            await send({"type": "http.response.body", "body": b"ok"})
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


# The InvoiceNinja OpenAPI spec ships alongside this server (baked into the
# image). Tools are generated from it at startup.
DEFAULT_SPEC = Path(__file__).parent / "invoiceninja-api-docs.yaml"


def _stringify_keys(obj):
    """Recursively coerce dict keys to strings.

    OpenAPI is a JSON format (JSON only has string keys), but this spec is
    authored as YAML, and YAML's default resolver parses unquoted numeric
    map keys -- e.g. the `200:`/`401:`/... status codes under `responses` --
    as ints rather than strings. The OpenAPI/pydantic validator FastMCP uses
    requires string keys, so we normalize back to JSON's key model here.
    """
    if isinstance(obj, dict):
        return {str(k): _stringify_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_keys(v) for v in obj]
    return obj


# operationId -> components/schemas name, for write operations the upstream
# spec documents with no requestBody at all (see _patch_missing_request_bodies).
# Each schema name was chosen to match the sibling operation on the same
# resource that *does* document a body (e.g. updateInvoice takes the same
# InvoiceRequest storeInvoice does; updateCompany/storeCompany have no such
# sibling anywhere, so both fall back to the plain response schema, Company,
# the same way the spec's own storePaymentTerm/storeTaskStatus do).
_MISSING_REQUEST_BODY_SCHEMAS = {
    "storeBankIntegration": "BankIntegration",
    "updateBankIntegration": "BankIntegration",
    "storeBankTransaction": "BankTransaction",
    "updateBankTransaction": "BankTransaction",
    "storeBankTransactionRule": "BankTransactionRule",
    "updateBankTransactionRule": "BankTransactionRule",
    "storeClientGatewayToken": "ClientGatewayToken",
    "updateClientGatewayToken": "ClientGatewayToken",
    "storeCompany": "Company",
    "updateCompany": "Company",
    "storeCompanyGateway": "CompanyGateway",
    "updateCompanyGateway": "CompanyGateway",
    "updateCompanyUser": "CompanyUser",
    "storeDesign": "Design",
    "updateDesign": "Design",
    "storeExpenseCategory": "ExpenseCategory",
    "updateExpenseCategory": "ExpenseCategory",
    "storeExpense": "Expense",
    "updateExpense": "Expense",
    "storeGroupSetting": "GroupSetting",
    "updateGroupSetting": "GroupSetting",
    "updatePaymentTerm": "PaymentTerm",
    "storeRecurringExpense": "RecurringExpense",
    "updateRecurringExpense": "RecurringExpense",
    "storeRecurringQuote": "RecurringQuote",
    "updateRecurringQuote": "RecurringQuote",
    "storeSubscription": "Subscription",
    "updateSubscription": "Subscription",
    "updateTaskStatus": "TaskStatus",
    "updateTaxRate": "TaxRate",
    "storeToken": "CompanyToken",
    "updateToken": "CompanyToken",
    "storeUser": "User",
    "updateUser": "User",
    "storeWebhook": "Webhook",
    "updateWebhook": "Webhook",
    "updateCredit": "CreditRequest",
    "updateRecurringInvoice": "RecurringInvoiceRequest",
    "updateInvoice": "InvoiceRequest",
    "updatePayment": "PaymentRequest",
}


def _patch_missing_request_bodies(spec: dict) -> None:
    """Fill in the requestBody the upstream spec omits for ~40 core CRUD
    write endpoints (companies, invoices, payments, users, webhooks, ...).

    Without this, FastMCP.from_openapi still generates a tool for e.g.
    `updateCompany` or `updateInvoice` -- it just has no body parameters at
    all, so calls silently no-op (200 OK, nothing changed) with no error to
    signal why. Verified by diffing every POST/PUT/PATCH operation in the
    bundled spec against whether it declares a requestBody: 40 CRUD
    operations don't, where a sibling operation on the same resource (or an
    equivalent resource, e.g. PaymentTerm/TaskStatus's own store operations)
    proves what shape the body should be. The remaining ~29 bodyless
    POST operations (refresh, webhooks, migration, self-update, invites,
    ...) are left untouched: those are genuinely parameterless actions, not
    another instance of this bug.

    Applied in-memory to the parsed spec after every load (see load_spec),
    not as an edit to the YAML file, because that file is periodically
    re-fetched from upstream and would silently drop a hand-edit on the next
    refresh. Each operationId is patched only if it's still missing a
    requestBody and its target schema still exists, so this is a no-op (not
    an error) if upstream ever documents these properly or renames a schema.
    """
    schemas = spec.get("components", {}).get("schemas", {})
    for methods in spec.get("paths", {}).values():
        if not isinstance(methods, dict):
            continue
        for op in methods.values():
            if not isinstance(op, dict):
                continue
            operation_id = op.get("operationId")
            schema_name = _MISSING_REQUEST_BODY_SCHEMAS.get(operation_id)
            if schema_name is None or "requestBody" in op:
                continue
            if schema_name not in schemas:
                print(
                    f"Warning: skipping requestBody patch for {operation_id!r} "
                    f"-- schema {schema_name!r} no longer exists in the spec.",
                    file=sys.stderr,
                )
                continue
            op["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": f"#/components/schemas/{schema_name}"}
                    }
                },
            }


def load_spec(spec_path: Path) -> dict:
    """Parse the on-disk InvoiceNinja OpenAPI spec into a dict.

    The spec ships as YAML; because YAML is a superset of JSON this also
    parses a JSON spec, so the file can be swapped for either format.
    """
    if not spec_path.exists():
        raise RuntimeError(f"OpenAPI spec not found at {spec_path}.")
    text = spec_path.read_text()
    if not text.strip():
        raise RuntimeError(
            f"OpenAPI spec at {spec_path} is empty -- populate it with the "
            f"InvoiceNinja OpenAPI spec."
        )
    spec = yaml.safe_load(text)
    if not isinstance(spec, dict):
        raise RuntimeError(f"OpenAPI spec at {spec_path} is not a valid mapping.")
    spec = _stringify_keys(spec)
    _patch_missing_request_bodies(spec)
    return spec


def register_health(mcp: FastMCP) -> None:
    """Add an unauthenticated health endpoint for container healthchecks."""

    @mcp.custom_route(HEALTH_PATH, methods=["GET"])
    async def health(_request: Request):
        return PlainTextResponse("ok")


def register_upload_client_tool(mcp: FastMCP, client: httpx.AsyncClient) -> None:
    """Register a working replacement for the generated uploadClient tool.

    The OpenAPI spec documents this as `POST /clients/{id}/upload`, but
    InvoiceNinja actually registers it (and every other `/{id}/upload` route)
    as PUT -- confirmed via `php artisan route:list` against a real instance.
    A raw PUT with a multipart/form-data body doesn't work either: PHP/Symfony
    only parse multipart bodies for POST requests, so a bare PUT upload is
    silently dropped (302, not even an error). The fix is Laravel's method-
    spoofing convention: send a POST with a `_method=PUT` field alongside the
    file. FastMCP's OpenAPI-generated tool also has no JSON-representable way
    to accept real file bytes from an MCP client, so we exclude it (see
    build_server's route_maps) and register a tool that takes base64-encoded
    file contents instead and builds the multipart request ourselves. The
    `client` carries the same per-request auth as the generated tools (see
    InvoiceNinjaTokenAuth).
    """

    @mcp.tool(name="uploadClient")
    async def upload_client(id: str, filename: str, content_base64: str) -> str:
        """Upload a document to a client.

        `content_base64` is the file's contents, base64-encoded. `filename`
        is the name to store it under; InvoiceNinja infers the file type from
        its extension.
        """
        file_bytes = base64.b64decode(content_base64)
        response = await client.post(
            f"/api/v1/clients/{id}/upload",
            files={
                "documents[]": (filename, file_bytes),
                "_method": (None, "PUT"),
            },
        )
        response.raise_for_status()
        return f"Uploaded {filename!r} to client {id!r}."


def build_server(client: httpx.AsyncClient | None = None) -> FastMCP:
    """Load the local OpenAPI spec and turn every documented InvoiceNinja
    endpoint into a FastMCP tool. The API token is supplied per request by the
    client (see TokenCaptureMiddleware / InvoiceNinjaTokenAuth), so no token
    is read here.

    `client` is injectable for testing; in production the default client
    targets INVOICENINJA_SERVER_URL and authenticates from the per-request
    contextvar.
    """
    if client is None:
        # No /api/v1 suffix here: unlike trillium-mcp's ETAPI spec (whose
        # path keys are relative, e.g. "/notes/{id}", with "/etapi" carried
        # only by the base URL), InvoiceNinja's spec path keys already
        # include the full "/api/v1/..." prefix (e.g. "/api/v1/clients").
        # httpx's base_url + relative-path concatenation would double it up
        # to "/api/v1/api/v1/clients" if we appended it here too.
        server_url = os.environ.get(SERVER_ENV, DEFAULT_SERVER_URL).rstrip("/")
        client = httpx.AsyncClient(
            base_url=server_url, auth=InvoiceNinjaTokenAuth(), timeout=60
        )

    spec_path = Path(os.environ.get(SPEC_ENV, str(DEFAULT_SPEC)))
    spec = load_spec(spec_path)

    # login/logout manage InvoiceNinja session tokens, but an MCP client
    # already authenticates via the Authorization header -- an LLM has no
    # reason to mint a token from a password, and calling logout would
    # invalidate its own credential. uploadClient is excluded and replaced
    # because FastMCP can't express real binary file bytes in its JSON
    # tool schema (see register_upload_client_tool).
    mcp = FastMCP.from_openapi(
        openapi_spec=spec,
        client=client,
        name="InvoiceNinja MCP",
        route_maps=[
            RouteMap(
                methods=["POST"],
                pattern=r"/(login|logout)$",
                mcp_type=MCPType.EXCLUDE,
            ),
            RouteMap(
                methods=["POST"],
                pattern=r"/clients/\{id\}/upload$",
                mcp_type=MCPType.EXCLUDE,
            ),
        ],
    )
    register_upload_client_tool(mcp, client)
    register_health(mcp)
    return mcp


def build_error_server(error: BaseException) -> FastMCP:
    """Stand-in MCP server that reports a startup failure over a live
    connection instead of dying with an opaque error. Only reachable if the
    bundled OpenAPI spec is missing or unparseable.
    """
    summary = str(error).strip() or error.__class__.__name__
    detail = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    ).strip()
    instructions = (
        f"This InvoiceNinja MCP server FAILED TO START and exposes no "
        f"InvoiceNinja tools.\n\nReason: {summary}\n\nThe bundled OpenAPI spec "
        f"could not be loaded. Call the `startup_error` tool for the full error."
    )
    mcp = FastMCP(
        name="InvoiceNinja MCP (startup failed)",
        instructions=instructions,
    )
    register_health(mcp)

    @mcp.tool
    def startup_error() -> str:
        """Explain why this InvoiceNinja MCP server failed to start."""
        return (
            "The InvoiceNinja MCP server failed to start, so no InvoiceNinja "
            f"tools are available.\n\n--- Full error ---\n{detail}"
        )

    return mcp


def serve(mcp: FastMCP) -> None:
    """Serve an MCP server over streamable HTTP behind the token-capture
    middleware, using the MCP_* environment configuration."""
    host = os.environ.get(MCP_HOST_ENV, DEFAULT_HOST)
    port = int(os.environ.get(MCP_PORT_ENV, DEFAULT_PORT))
    path = os.environ.get(MCP_PATH_ENV, DEFAULT_PATH)

    allowed = os.environ.get(MCP_ALLOWED_HOSTS_ENV, "").strip()
    if allowed:
        hosts = [h.strip() for h in allowed.split(",") if h.strip()]
        inner = mcp.http_app(path=path, allowed_hosts=hosts, host_origin_protection=True)
        print(f"Host protection ON; allowed hosts (plus localhost): {hosts}",
              file=sys.stderr)
    else:
        inner = mcp.http_app(path=path, host_origin_protection=False)
        print(f"Host protection OFF (any Host accepted) -- set "
              f"{MCP_ALLOWED_HOSTS_ENV} to restrict.", file=sys.stderr)
    app = TokenCaptureMiddleware(inner)

    print(f"Serving InvoiceNinja MCP on http://{host}:{port}{path} "
          f"(client supplies the API token via the Authorization header)",
          file=sys.stderr)
    uvicorn.run(app, host=host, port=port)


def main():
    try:
        mcp = build_server()
    except Exception as e:
        print(f"Error: failed to build InvoiceNinja MCP server: {e}",
              file=sys.stderr)
        mcp = build_error_server(e)
    serve(mcp)


if __name__ == "__main__":
    main()
