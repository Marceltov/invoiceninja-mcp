# InvoiceNinja MCP server

A standalone [MCP](https://modelcontextprotocol.io) server that exposes the [InvoiceNinja](https://invoiceninja.com) v5 REST API as MCP tools. It runs as a **container sidecar** next to your InvoiceNinja instance: nearly every documented API endpoint is turned into an MCP tool at startup via `FastMCP.from_openapi` (**363 tools**, generated from the spec's 365 documented operations — `storeInvoice`, `getClients`, `showInvoice`, `storeClient`, …; the session endpoints `login`/`logout` are excluded), served over streamable **HTTP** so any MCP client connects to it by URL.

## Architecture

**invoiceninja-mcp** (this repo) runs as a container sidecar and talks to InvoiceNinja over the internal Docker network, so InvoiceNinja's API is never exposed publicly on its own. Clients reach invoiceninja-mcp either through a TLS-terminating reverse proxy or directly over a trusted LAN — in both cases the API token they present is the only credential.

## Quick start

**1. Create an API token in InvoiceNinja** — *Settings → Account Management → Integrations → API tokens*. This token is the only credential: invoiceninja-mcp stores no secret and forwards it to InvoiceNinja as `X-API-TOKEN` (alongside a required `X-Requested-With: XMLHttpRequest` header). Each client presents its own token per request.

**2. Add invoiceninja-mcp to your InvoiceNinja's `docker-compose.yaml`** — one service, pulling the prebuilt image, so there's nothing to clone or build:

```yaml
services:
  invoiceninja:
    # ... your existing InvoiceNinja service ...

  invoiceninja-mcp:
    image: ghcr.io/marceltov/invoiceninja-mcp:latest
    container_name: invoiceninja-mcp
    restart: unless-stopped
    environment:
      # Service name of your existing InvoiceNinja on the same compose network.
      INVOICENINJA_SERVER_URL: http://invoiceninja:80
    ports:
      - "8081:8081"
```

Then start it:

```bash
docker compose up -d invoiceninja-mcp
```

Both services share the compose network, so `invoiceninja` resolves to your existing container. If your InvoiceNinja runs elsewhere (a separate compose project or host), point `INVOICENINJA_SERVER_URL` at a URL this container can reach and attach it to the right network. The MCP endpoint is then available at `http://localhost:8081/mcp`.

**3. Connect your MCP client** with the token from step 1:

```bash
claude mcp add invoiceninja --scope user --transport http \
  http://localhost:8081/mcp \
  --header "Authorization: YOUR_INVOICENINJA_API_TOKEN"
```

The `--scope user` flag registers the server across **all** your projects. Drop it to fall back to `claude mcp add`'s default **local** scope.

Alternatively, use the provided [`.mcp.json`](.mcp.json), filling in your host and token.

## Configuration

All configuration is via environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `INVOICENINJA_SERVER_URL` | `http://invoiceninja:80` | Base URL of the InvoiceNinja instance, used **as-is** (the spec's own paths already include `/api/v1`, so no suffix is appended). |
| `MCP_HOST` | `0.0.0.0` | Interface the MCP server binds to. |
| `MCP_PORT` | `8081` | Port the MCP server listens on. |
| `MCP_PATH` | `/mcp` | HTTP path the MCP endpoint is served at. |
| `INVOICENINJA_API_SPEC` | bundled spec | Override the OpenAPI spec path. |
| `MCP_ALLOWED_HOSTS` | *(unset = any)* | Comma-separated `Host` allowlist (DNS-rebinding protection). |

## TLS / reverse proxy

The container serves plain HTTP on `:8081`; terminate TLS at your reverse proxy. Example Caddyfile:

```
your-host {
    reverse_proxy mcp:8081
}
```

## Security

The MCP endpoint grants **full access to your InvoiceNinja account** — whatever the token's permissions allow. Every request must carry a valid API token in the `Authorization` header; requests with no `Authorization` header at all are rejected with `401` before reaching any tool. The server never validates the token itself — validity is enforced by InvoiceNinja when the forwarded request reaches the actual API call, and the server holds no secret of its own. The `/health` endpoint is always unauthenticated (used by the container healthcheck).

The token is sent as `X-API-TOKEN` (alongside `X-Requested-With: XMLHttpRequest`) on every call. Over plain HTTP it travels in cleartext, so either keep traffic on a **trusted network** (e.g. a LAN or the Docker network) or put TLS in front.

By default the server accepts requests for **any** `Host` (DNS-rebinding protection is disabled), so it can be reached by LAN IP or by the domain your reverse proxy forwards. To lock this down, set `MCP_ALLOWED_HOSTS` to a comma-separated list of the host[:port] values you actually use.

If the OpenAPI spec cannot be loaded at startup, the server still starts and completes the MCP handshake, but exposes only a single `startup_error` tool describing how to fix it.

## How it works

`TokenCaptureMiddleware` requires an `Authorization` header on the MCP path (stripping an optional `Bearer ` prefix) and stashes it in a contextvar; `InvoiceNinjaTokenAuth` reads it on the outgoing call and sets it as `X-API-TOKEN`, alongside a fixed `X-Requested-With: XMLHttpRequest` header. Validity is enforced by InvoiceNinja itself — this server never validates the token.

## Contributing

For local development there's a ready-to-run stack — a throwaway InvoiceNinja instance plus the MCP server built from local source. See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup and how to run the tests.

## License

Copyright © 2026 Marcel Bruckner.

Licensed under the [GNU Affero General Public License v3.0 or later](LICENSE) (AGPL-3.0-or-later). You may use, modify, and redistribute it, but any modified version — **including one you run as a network service** — must be released under the same license with its source made available to its users, and the original copyright notice preserved. See [LICENSE](LICENSE) for the full terms.
