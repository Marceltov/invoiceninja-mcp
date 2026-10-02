# How it works

The whole server is one module, `app/server.py`.

## Startup

`main()` picks the spec (see [Configuration](configuration.md#which-api-spec-is-used)), patches it in memory (`_patch_missing_request_bodies`, `_patch_design_schema`, `_patch_request_bodies` — no-ops where upstream is already correct), then `FastMCP.from_openapi` generates one tool per operation. If the spec can't be loaded, `build_error_server()` serves a lone `startup_error` tool.

## Token pass-through

```mermaid
sequenceDiagram
    participant C as MCP client
    participant M as TokenCaptureMiddleware
    participant T as FastMCP tool
    participant A as InvoiceNinjaTokenAuth
    participant I as InvoiceNinja
    C->>M: request + Authorization header
    M->>M: 401 if header missing, else store in contextvar
    M->>T: call tool
    T->>A: outgoing httpx request
    A->>I: X-API-TOKEN + X-Requested-With: XMLHttpRequest
```

1. `TokenCaptureMiddleware` (pure ASGI) requires `Authorization` on the MCP path and stashes it in a contextvar.
2. `InvoiceNinjaTokenAuth` (an `httpx.Auth`) reads it on the outgoing call, strips a leading `Bearer `, and sets `X-API-TOKEN`, plus the fixed `X-Requested-With` header InvoiceNinja's docs require.

## OAuth

In `oauth` and `both` modes the server is also an authorization server (the MCP SDK's `OAuthProvider`, subclassed as `InvoiceNinjaOAuthProvider`) with an encrypted file store under `/data/oauth`.

```mermaid
sequenceDiagram
    participant C as MCP client
    participant M as invoiceninja-mcp
    participant U as User browser
    participant I as InvoiceNinja
    C->>M: request without token
    M-->>C: 401 + resource_metadata
    C->>M: discovery and POST /register
    C->>U: open /authorize
    U->>M: /authorize then login form
    U->>M: email, password, optional 2FA
    M->>I: POST /api/v1/login
    I-->>M: session token
    M->>I: POST /api/v1/tokens named MCP client
    I-->>M: minted token
    M-->>U: redirect with code and iss
    U->>C: code
    C->>M: POST /token with PKCE
    M-->>C: inmcp_ access token
    C->>M: tool call
    M->>I: X-API-TOKEN minted token
```

1. An unauthenticated request gets `401` with a `resource_metadata` pointer. The client discovers the endpoints and registers itself (Dynamic Client Registration).
2. `/authorize` hands the user to `/login`, where InvoiceNinja checks the credentials. The server mints a per-client token with the session token and never calls `/logout`.
3. The redirect carries the authorization code and `iss`. The client exchanges the code (PKCE) for an `inmcp_` access token and a refresh token.
4. On a tool call the server maps the access token to the minted InvoiceNinja token and sends it as `X-API-TOKEN`.

`resolve_auth_mode()` picks `token`, `oauth` or `both` from the environment, and `wrap_app()` assembles the ASGI stack for it. In both `oauth` and `both`, `IssuerFlagMiddleware` advertises `iss` support in the metadata and `UploadAuthMiddleware` gates the plain-HTTP `/upload/{entity}/{id}` route with the same bearer. Only in `both` does `BearerPrefixMiddleware` add `Bearer ` to raw tokens so FastMCP can parse them.
