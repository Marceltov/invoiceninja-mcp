# invoiceninja-mcp design

## Summary

A standalone MCP server that exposes the [InvoiceNinja](https://invoiceninja.com) v5 REST API as MCP tools, built as a sibling project to [trillium-mcp](https://github.com/Marceltov/trillium-mcp) and reusing its architecture almost line-for-line: one Python module, tools generated at startup from a bundled OpenAPI spec via `FastMCP.from_openapi`, served over streamable HTTP, run as a Docker sidecar next to a self-hosted InvoiceNinja instance, with the client's own API token forwarded per request and no secret stored server-side.

## Why mirror trillium-mcp

trillium-mcp already solved the hard parts of this pattern — OpenAPI-to-MCP generation, per-request token pass-through with no stored secret, ASGI-level auth middleware that doesn't buffer streamable HTTP, startup resilience when the spec can't load, and a unit/live test split with a coverage guard. InvoiceNinja's API is a good match for the same approach: it is fully described by a maintained OpenAPI 3.0.1 spec and authenticates with a single static header token, so none of Trilium's session/login complexity needs reinventing.

## Source spec

InvoiceNinja publishes a maintained OpenAPI 3.0.1 spec at [`invoiceninja/api-docs`](https://github.com/invoiceninja/api-docs) (`api-docs.yaml`, built with Redocly). As of this writing:

- **271 paths / 365 operations** covering invoices, clients, quotes, credits, payments, products, tasks, projects, expenses, vendors, purchase orders, recurring invoices/expenses, bank integrations, reports, and more.
- **Single security scheme**: `ApiKeyAuth`, an API key in the `X-API-TOKEN` header (`components.securitySchemes.ApiKeyAuth`).
- **Servers**: `https://demo.invoiceninja.com` (documented demo token `TOKEN`) and `https://invoicing.co` (hosted production).
- **No non-JSON response bodies** declared anywhere in the spec — unlike Trilium's ZIP-export and HTML-content problems, there is nothing here that structurally breaks FastMCP's JSON-in/JSON-out assumption on the response side.
- **16 `multipart/form-data` upload endpoints** (`POST /invoices/{id}/upload`, `/expenses/{id}/upload`, `/clients/{id}/upload`, etc.) — the InvoiceNinja analog of Trilium's text/plain PUT problem. Whether FastMCP's generated tools handle these correctly is unverified; this is the primary implementation risk (see Open Questions).
- `/api/v1/login` and `/api/v1/logout` exist and manage session tokens, structurally identical to Trilium's excluded auth endpoints.

The spec ships vendored into the repo (`app/invoiceninja-api-docs.yaml`), matching how `app/trilium-etapi.openapi` is vendored today, with an env var override for a custom path.

## Architecture

Single Python module `app/server.py`, same shape as trillium-mcp's:

1. `build_server()` loads the vendored OpenAPI spec and calls `FastMCP.from_openapi()`, generating one tool per operation.
2. A pure-ASGI `TokenCaptureMiddleware` requires an `Authorization` header on the MCP path, rejects requests without one as `401` before FastMCP sees them, and stashes the header in a contextvar. `/health` stays unauthenticated for the container healthcheck.
3. An `httpx.Auth` implementation reads that contextvar on the outgoing call to InvoiceNinja and sets it as the `X-API-TOKEN` header (see Auth mapping below).
4. `serve()` runs the app under `uvicorn`, with the same `MCP_ALLOWED_HOSTS`-gated DNS-rebinding protection as Trilium (off by default; the token is the real gate).
5. `build_error_server()` provides the same startup-resilience fallback: if the spec fails to load, the server still completes the MCP handshake and exposes a single `startup_error` tool instead of dying with an opaque connection error.

## Auth mapping

This is the one structural divergence from Trilium. Trilium's ETAPI already uses an `Authorization` header, so the client's header passes straight through unchanged. InvoiceNinja instead expects `X-API-TOKEN`. To keep the client-facing convention identical to trillium-mcp (and consistent with the normal `claude mcp add --header "Authorization: ..."` usage pattern), the MCP endpoint still requires an incoming `Authorization` header, stripping an optional `Bearer ` prefix exactly as Trilium does — but the outgoing `httpx.Auth` sets `X-API-TOKEN` instead of forwarding the header name unchanged. `TokenCaptureMiddleware` is otherwise unmodified from Trilium's.

`/api/v1/login` and `/api/v1/logout` are excluded via `RouteMap`, same rationale as Trilium: an MCP client already authenticates via the header, has no reason to mint a token from a password, and calling logout would invalidate its own credential.

## Endpoint fixups

Known at design time:

- **`login`/`logout`**: excluded outright (see Auth mapping).

To be discovered during implementation, following Trilium's playbook of "exclude via `RouteMap` + hand-written replacement tool" for whatever breaks:

- **The 16 multipart upload endpoints**: primary risk area. FastMCP may generate working tools for these (e.g. a `file` parameter FastMCP base64-decodes into a multipart part) or may not; verified by a live test against each, fixed the same way Trilium fixed its text/plain PUT endpoints if broken.
- Any other endpoint-specific breakage surfaced by the coverage-guard-driven live test suite (see Testing) gets the same treatment: understand which layer it breaks at (metadata vs. behavior, per Trilium's `CLAUDE.md` framing), fix via `mcp_component_fn` if it's metadata-only, exclude+replace if it's a real behavior mismatch.

No fixups are pre-designed beyond login/logout — Trilium's own experience shows these are found by running the real spec against a real instance, not by static spec inspection.

## Configuration (env vars)

Directly parallel to Trilium's set:

| Variable | Default | Purpose |
|---|---|---|
| `INVOICENINJA_SERVER_URL` | `http://invoiceninja:80` | Base URL of the InvoiceNinja instance (`/api/v1` appended automatically). |
| `MCP_HOST` | `0.0.0.0` | Interface the MCP server binds to. |
| `MCP_PORT` | `8081` | Port the MCP server listens on. |
| `MCP_PATH` | `/mcp` | HTTP path the MCP endpoint is served at. |
| `INVOICENINJA_API_SPEC` | bundled spec | Override the OpenAPI spec path. |
| `MCP_ALLOWED_HOSTS` | *(unset = any)* | Comma-separated `Host` allowlist (DNS-rebinding protection). |

`INVOICENINJA_SERVER_URL` defaults sidecar-style (self-hosted assumption), matching Trilium; a user on the hosted SaaS points it at `https://invoicing.co` instead — same override pattern the config table already documents.

## Dev fixture

`docker-compose.yaml` at the repo root, heavier than Trilium's single-container fixture because self-hosted InvoiceNinja needs three services:

- `nginx` (or the app's built-in web server) fronting the app container.
- `invoiceninja/invoiceninja:5` (official image) — needs `APP_KEY`, `APP_URL`, DB env vars.
- `mysql:8` (or MariaDB) as the database.

Unlike Trilium, there is no pre-seeded "demo notes" image to commit as a fixture — InvoiceNinja needs a one-time setup step (via the app's install wizard or an `artisan` seed/tinker command run once) to create a company, user, and API token. That token gets committed the same way `etapi.token` is today (`invoiceninja.token`), scoped to the disposable dev instance only, never reused anywhere real. The exact seeding mechanism (install API vs. artisan command vs. a custom seeder) is an implementation-time decision, not a design-time one — it depends on what the official image actually supports non-interactively.

`APP_KEY` generation (`php artisan key:generate` equivalent) and any other one-time init needed to bring the fixture from empty containers to "ready for live tests" belongs in `CONTRIBUTING.md`, mirroring Trilium's documented dev setup.

## Testing

Same split as Trilium, under `app/tests/`:

- **Unit tests** (`app/tests/test_*.py`): drive `server.py` through a mock httpx transport — no running InvoiceNinja. Cover the token-forwarding chain (header capture, `Bearer` stripping, `X-API-TOKEN` mapping) and any special-cased tools.
- **Live integration tests** (`app/tests/live/`): drive the real MCP server over HTTP against the running compose stack, covering every MCP tool; auto-skip when `/health` is unreachable.
- **Coverage guard** (`test_coverage.py`): fails if any spec `operationId` (minus excluded `login`/`logout`) lacks a live test, and if tests reference unknown tool names — this is what actually surfaces the multipart-upload risk area during implementation rather than requiring it to be fully pre-audited now.

`./run-tests.sh` orchestrates reset → build+start → wait-for-health → pytest → teardown+reset, matching Trilium's script.

## Docs / publishing

README, `CLAUDE.md`, `CONTRIBUTING.md` structured like Trilium's (Architecture / Quick start / Connecting a client / Configuration / TLS / Security / How it works / Alternatives / Contributing / License sections). GHCR image published by the same GitHub Actions pattern (branch pushes → `:<branch>` tag, `v*` tags → versioned release + `latest`). License: AGPL-3.0-or-later, matching Trilium. Repo: `Marceltov/invoiceninja-mcp`, currently empty.

The README's "Alternatives" table (existing InvoiceNinja MCP servers, if any, compared on transport/auth/tool-generation) is a nice-to-have for parity with Trilium's README but not load-bearing for the design — filled in during implementation from a quick survey, not researched further here.

## Out of scope

- A client-side companion plugin (like `trilium-plugin`) bundling ready-made skills — not requested; can be a separate follow-on project once the server exists.
- Curating the tool surface — explicitly rejected in favor of full spec coverage (365 tools), matching Trilium's stated philosophy of generating from the spec rather than hand-picking.
- Multi-company token scoping nuances beyond what a single `X-API-TOKEN` already encodes — InvoiceNinja tokens are already scoped to a user/company by the server; nothing extra needed here.

## Open questions (resolved during implementation, not blocking this spec)

- Exact seeding mechanism for the dev fixture's company/user/API token.
- Whether any of the 16 multipart upload endpoints need exclude+replace treatment, and if so, what FastMCP actually does with `multipart/form-data` request bodies today.
- Whether `X-Requested-With: XMLHttpRequest` is actually enforced by InvoiceNinja on token-authenticated API calls (some docs mention it for CSRF protection on web routes; unconfirmed whether the pure API path requires it) — if it does, `InvoiceNinjaTokenAuth` sets it unconditionally alongside `X-API-TOKEN`.
