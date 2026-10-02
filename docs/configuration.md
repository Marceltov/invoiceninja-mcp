# Configuration

All configuration is via environment variables.

| Variable | Default | Purpose |
| --- | --- | --- |
| `INVOICENINJA_SERVER_URL` | `http://invoiceninja:80` | Base URL of the InvoiceNinja instance, used **as-is** (the spec's paths already include `/api/v1`, so no suffix is appended). |
| `MCP_HOST` | `0.0.0.0` | Interface the MCP server binds to. |
| `MCP_PORT` | `8081` | Port the MCP server listens on. |
| `MCP_PATH` | `/mcp` | HTTP path the MCP endpoint is served at. |
| `INVOICENINJA_API_SPEC` | *(unset)* | Path to a fixed OpenAPI spec file; skips the version-matched download. |
| `MCP_ALLOWED_HOSTS` | *(unset = any)* | Comma-separated `Host` allowlist (DNS-rebinding protection). |
| `MCP_AUTH_MODE` | *(unset)* | `token`, `oauth` or `both`. Unset means `both` when `MCP_BASE_URL` and `MCP_OAUTH_SECRET` are set, else `token`. |
| `MCP_BASE_URL` | *(unset)* | Public HTTPS URL clients use to reach the server (the OAuth issuer). `localhost` may use http. |
| `MCP_OAUTH_SECRET` | *(unset)* | Any long random string. Encrypts the OAuth store at rest at `/data/oauth`. Changing it logs every client out. |

## OAuth

To let app clients log in with OAuth, give the server a public HTTPS URL, a secret, and a volume for its store:

```yaml
services:
  invoiceninja-mcp:
    environment:
      MCP_BASE_URL: https://invoiceninja-mcp.example.com
      MCP_OAUTH_SECRET: <openssl rand -hex 32>
    volumes:
      - mcp-oauth:/data

volumes:
  mcp-oauth:
```

With both variables set the mode defaults to `both`: OAuth clients and raw-token clients work side by side. Set `MCP_AUTH_MODE=oauth` to refuse raw tokens, or `token` to turn OAuth off. See [Connecting clients](connecting.md) and [Security](security.md).

## Which API spec is used

Tools are generated from the OpenAPI spec matching **your** InvoiceNinja version. At startup the server:

1. Uses `INVOICENINJA_API_SPEC` if set.
2. Otherwise reads `X-APP-VERSION` from an unauthenticated `/api/v1/ping` and downloads that release's spec from `raw.githubusercontent.com/invoiceninja/invoiceninja/v<version>/openapi/api-docs.yaml`.
3. On any failure (no internet, instance not up, unknown version) falls back to the bundled spec (currently 5.13.43) and logs a warning.

!!! tip "Offline setups"
    Any InvoiceNinja image contains its own spec at `/var/www/html/openapi/api-docs.yaml`; mount it and set `INVOICENINJA_API_SPEC` to pin it.

If no spec can be loaded at all, the server still completes the MCP handshake but exposes only a `startup_error` tool describing the failure.
