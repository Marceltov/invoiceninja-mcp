# Getting started

## 1. Create an API token

In InvoiceNinja go to *Settings → Account Management → Integrations → API tokens*. This is the only credential: the server stores no secret and forwards your token to InvoiceNinja as `X-API-TOKEN`. Each client presents its own token per request.

## 2. Add the sidecar

Add one service to your InvoiceNinja `docker-compose.yaml`. It uses the prebuilt image, so there is nothing to clone or build.

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

```bash
docker compose up -d invoiceninja-mcp
curl http://localhost:8081/health   # -> ok
```

If InvoiceNinja runs elsewhere, point `INVOICENINJA_SERVER_URL` at a URL the container can reach and attach it to the right network. The MCP endpoint is `http://localhost:8081/mcp`.

## 3. Connect a client

```bash
claude mcp add invoiceninja --scope user --transport http \
  http://localhost:8081/mcp \
  --header "Authorization: YOUR_INVOICENINJA_API_TOKEN"
```

`--scope user` registers the server across all your projects; drop it for the default local scope. Or use the repo's [`.mcp.json`](https://github.com/Marceltov/invoiceninja-mcp/blob/main/.mcp.json), filling in host and token.

## TLS / reverse proxy

The container serves plain HTTP on `:8081`; terminate TLS in front of it. Example Caddyfile:

```
your-host {
    reverse_proxy invoiceninja-mcp:8081
}
```
