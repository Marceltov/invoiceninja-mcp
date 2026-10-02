# How it works

The whole server is one module, `app/server.py`.

## Startup

`main()` picks the spec (see [Configuration](configuration.md#which-api-spec-is-used)), patches it in memory (`_patch_missing_request_bodies`, `_patch_design_schema`, `_patch_request_bodies` — no-ops where upstream is already correct), then `FastMCP.from_openapi` generates one tool per operation. If the spec can't be loaded, `build_error_server()` serves a lone `startup_error` tool.

## Token pass-through

```mermaid
sequenceDiagram
    participant C as MCP client
    participant M as TokenCaptureMiddleware
    participant T as Tool (FastMCP)
    participant A as InvoiceNinjaTokenAuth
    participant I as InvoiceNinja
    C->>M: request + Authorization header
    M->>M: 401 if header missing; store in contextvar
    M->>T: call tool
    T->>A: outgoing httpx request
    A->>I: X-API-TOKEN + X-Requested-With: XMLHttpRequest
```

1. `TokenCaptureMiddleware` (pure ASGI) requires `Authorization` on the MCP path and stashes it in a contextvar.
2. `InvoiceNinjaTokenAuth` (an `httpx.Auth`) reads it on the outgoing call, strips a leading `Bearer `, and sets `X-API-TOKEN`, plus the fixed `X-Requested-With` header InvoiceNinja's docs require.
