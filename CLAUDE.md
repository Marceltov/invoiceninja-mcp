# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standalone MCP server that turns the [InvoiceNinja](https://invoiceninja.com) v5 REST API into MCP tools. It runs as a **container sidecar** next to an InvoiceNinja instance and exposes ~377 tools generated at startup from the OpenAPI spec matching the instance's version (379 documented operations in 5.13.43, minus `login`/`logout`), served over streamable **HTTP**. In token mode it stores no secret: each client presents its own API token in the `Authorization` header, which is forwarded per-request to InvoiceNinja as `X-API-TOKEN` (alongside a fixed `X-Requested-With: XMLHttpRequest` header). In `oauth`/`both` modes it is also an OAuth authorization server whose login mints a per-client InvoiceNinja token.

The entire server is one module: `app/server.py`.

## Commands

Development uses `uv` (in `app/`) and Docker Compose (at the repo root).

```bash
# Run the full test suite against a fresh, disposable fixture (recommended).
# Wipes fixture volumes, rebuilds+starts the stack, waits for health, mints a
# dev token, runs pytest, then tears down and wipes volumes again — even on
# failure.
./run-tests.sh
./run-tests.sh tests/live -q     # extra args pass through to pytest
./run-tests.sh -k clients        # a single test by keyword

# Manual test run (from app/). Live tests auto-skip when the stack is down.
cd app && uv run pytest
cd app && uv run pytest -k test_strips_bearer_prefix   # single test

# Dev stack: throwaway InvoiceNinja (app+nginx+mysql+redis) + MCP built from
# local source.
docker compose up -d --build --wait
curl http://localhost:8081/health   # -> ok
python3 scripts/mint_dev_token.py   # mint invoiceninja.token

# Reset the fixture to a pristine state (named volumes, not bind mounts —
# "reset" means wiping the volumes, not `git checkout`):
docker compose down -v && docker compose up -d --wait
```

The image is built and pushed to GHCR by `.github/workflows/publish.yml`: every branch push produces a testable image tagged `:<branch>`, and a `v*` tag produces the versioned release plus a GitHub release. `cleanup-packages.yml` weekly prunes orphaned untagged manifests.

## Test layout

Two kinds of tests, both under `app/tests/`:

- **Unit tests** (`app/tests/test_*.py`) drive `server.py` through a mock httpx transport — no running InvoiceNinja. These verify the token-forwarding chain (`test_token_auth`, `test_middleware`, `test_integration`) and the special-cased tools (`test_build_server`).
- **Live integration tests** (`app/tests/live/`) drive the real MCP server over HTTP against the running stack. They **auto-skip** when the MCP `/health` endpoint is unreachable (see `conftest.py` + `_client.stack_reachable`).

`test_coverage.py` is a **scoped guard**: unlike trillium-mcp's guard (which requires every ETAPI operationId to have a live test, feasible for its ~38 tools), this one only requires the operationIds listed in `_TESTED_OPERATIONS` to have a live test — currently `getClients`, `storeClient`, `showClient`, `updateClient`, `deleteClient`, `storeInvoice`, `showInvoice`, and `uploadClient`. InvoiceNinja's ~379-operation surface is deliberately covered incrementally. Expanding coverage means adding the operationId to `_TESTED_OPERATIONS` and a matching live test in the same change.

## Architecture

`main()` picks the spec via `resolve_spec()`: an explicit `INVOICENINJA_API_SPEC` file wins; otherwise `fetch_instance_spec()` reads `X-APP-VERSION` from an unauthenticated `/api/v1/ping` and downloads that release's spec from GitHub; on any failure it falls back to the bundled `app/invoiceninja-api-docs.yaml` (refresh with `scripts/update_spec.sh <version>`). Unit tests always build from the bundled file. `build_server()` then calls `FastMCP.from_openapi` on it, generating one tool per operation. Every spec is patched in-memory at parse time (`_patch_missing_request_bodies`, `_patch_design_schema`, `_patch_request_bodies`); the patches are no-ops where upstream is already correct, so they're safe across versions. The spec is treated as a hint, not a gate (ADR 0002): body `required` lists are dropped, output validation is off, and `apiRequest` (`register_api_request_tool`) is the escape hatch for fields/endpoints the spec gets wrong (binary responses such as PDF downloads come back as an embedded file resource, not mangled text) — FastMCP silently drops arguments a generated tool doesn't declare, so a raw pass-through beats patching each gap. Prefer `apiRequest` over adding another targeted patch unless the field is commonly needed. Two kinds of exclusions:

- **`login`/`logout`** — excluded outright (RouteMap, no replacement): they manage session tokens, but an MCP client already authenticates via the header, and logout would invalidate its own credential.
- **`uploadClient`** — excluded and **replaced** (`register_upload_client_tool`). The spec documents `POST /clients/{id}/upload` with a `multipart/form-data` body with binary file parts, which FastMCP's JSON-in/JSON-out tool generation can't express in the first place — but the spec's method is also wrong: InvoiceNinja actually registers this route (and every other `/{id}/upload` route) as **PUT**, confirmed against a real instance via `php artisan route:list`. A raw PUT with a multipart body doesn't work either — PHP/Symfony only parse multipart bodies on POST, so a bare PUT upload is silently dropped (302, no error). The replacement tool works around both problems: it takes base64-encoded file content, then sends a **POST** with a `_method: "PUT"` field alongside the file (Laravel's method-spoofing convention) to get a route match with a body Laravel actually parses. The other 15 multipart upload endpoints in the spec (`/invoices/{id}/upload`, `/expenses/{id}/upload`, etc.) are **not yet** fixed — only `uploadClient` has been verified against a real instance. If you need another resource's upload endpoint, verify it the same way (write a live test first; if the generated tool fails, add an exclude + replacement following `register_upload_client_tool`'s pattern) and add its operationId to `test_coverage.py`'s `_TESTED_OPERATIONS`.

### Token pass-through (the auth model)

In token mode the server holds no secret. The API token travels per-request through a contextvar:

1. `TokenCaptureMiddleware` (pure-ASGI) requires an `Authorization` header on the MCP path, rejecting requests without one as `401` before FastMCP sees them. `/health` is always allowed through unauthenticated.
2. `InvoiceNinjaTokenAuth` (an `httpx.Auth`) reads the contextvar on the outgoing InvoiceNinja call, strips a leading `Bearer ` if present, and sets it as `X-API-TOKEN`. It also sets a fixed `X-Requested-With: XMLHttpRequest` header, which InvoiceNinja's API docs describe as required.

### OAuth

`InvoiceNinjaOAuthProvider` (subclass of the MCP SDK `OAuthProvider`) serves discovery, DCR, `/authorize`, `/token` (PKCE), `/revoke`, plus its own `/login` form. `/login` posts email, password and optional 2FA code to InvoiceNinja `/api/v1/login`, then mints a token named `MCP: <client>` via `/api/v1/tokens` with the session token. It **never** calls `/logout` (that deletes the user's shared "User Token"). Registrations and tokens live in a Fernet-encrypted store at `/data/oauth` keyed by `MCP_OAUTH_SECRET`; OAuth bearers carry the `inmcp_` prefix. `resolve_auth_mode()` picks `token|oauth|both` (unset means `both` when `MCP_BASE_URL` and `MCP_OAUTH_SECRET` are set, else `token`) and `wrap_app()` builds the ASGI stack: in both `oauth` and `both`, `UploadAuthMiddleware` gates `/upload/{entity}/{id}` and `IssuerFlagMiddleware` advertises `iss`; only `both` adds `BearerPrefixMiddleware`, which lets raw tokens through. Revoke drops the OAuth pair and best-effort `DELETE /api/v1/tokens/{id}`, which only archives the token. Tests: `app/tests/test_oauth.py` and `app/tests/live/test_oauth_flow.py`. 2FA and passkey/SSO logins are untested; CIMD is unsupported (DCR only). See ADR 0003.

### Startup resilience

If the OpenAPI spec can't be loaded, `main()` falls back to `build_error_server()`, which completes the MCP handshake but exposes only a `startup_error` tool describing the failure.

### Configuration

All config is environment variables (no CLI args): `INVOICENINJA_SERVER_URL` (used as-is; the spec's own paths already include `/api/v1`), `MCP_HOST`, `MCP_PORT`, `MCP_PATH`, `INVOICENINJA_API_SPEC`, `MCP_ALLOWED_HOSTS`, `MCP_AUTH_MODE`, `MCP_BASE_URL`, `MCP_OAUTH_SECRET` (OAuth; see above).

## Dev fixture credentials

The seeded InvoiceNinja at http://localhost:8082 uses `admin@example.com` / `invoiceninja-mcp-dev` — a **committed dev fixture credential** (set via `IN_USER_EMAIL`/`IN_PASSWORD` in `docker-compose.yaml`), scoped to the disposable instance, never reuse it anywhere real. The API token itself is *not* committed: it's minted fresh on each fixture bring-up by `scripts/mint_dev_token.py` into `invoiceninja.token` (gitignored) — unlike trillium-mcp's committed `etapi.token`, this one can't be static since a fresh container boot creates a new token.

The dev compose stack also sets `MCP_BASE_URL`/`MCP_OAUTH_SECRET` for the MCP service and mounts a `mcp-oauth:/data` volume, so OAuth works against the fixture.
