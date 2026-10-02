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

## Which API spec is used

Tools are generated from the OpenAPI spec matching **your** InvoiceNinja version. At startup the server:

1. Uses `INVOICENINJA_API_SPEC` if set.
2. Otherwise reads `X-APP-VERSION` from an unauthenticated `/api/v1/ping` and downloads that release's spec from `raw.githubusercontent.com/invoiceninja/invoiceninja/v<version>/openapi/api-docs.yaml`.
3. On any failure (no internet, instance not up, unknown version) falls back to the bundled spec (currently 5.13.43) and logs a warning.

!!! tip "Offline setups"
    Any InvoiceNinja image contains its own spec at `/var/www/html/openapi/api-docs.yaml`; mount it and set `INVOICENINJA_API_SPEC` to pin it.

If no spec can be loaded at all, the server still completes the MCP handshake but exposes only a `startup_error` tool describing the failure.
