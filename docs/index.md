# InvoiceNinja MCP

A standalone [MCP](https://modelcontextprotocol.io) server that exposes the [InvoiceNinja](https://invoiceninja.com) v5 REST API as MCP tools, so any MCP client (Claude Code, Claude Desktop, …) can manage clients, invoices, quotes, payments and more.

It runs as a **container sidecar** next to your InvoiceNinja instance and serves **streamable HTTP**. Nearly every documented API endpoint becomes a tool at startup: **377 tools** against InvoiceNinja 5.13.43, generated from the spec's 379 operations (`login`/`logout` are excluded).

<div class="grid cards" markdown>

- :material-rocket-launch: **[Getting started](getting-started.md)** — add one service to your compose file and connect a client.
- :material-key: **[Stateless auth](security.md)** — no secret stored; each client sends its own API token.
- :material-tools: **[Tools](tools.md)** — generated tools, plus `apiRequest` and `uploadClient`.
- :material-cog: **[Configuration](configuration.md)** — environment variables and spec selection.

</div>

## Why a sidecar

InvoiceNinja's API stays on the internal Docker network. Clients reach the MCP server through a TLS-terminating reverse proxy or a trusted LAN, and the API token they present is the only credential.

## License

[AGPL-3.0-or-later](https://github.com/Marceltov/invoiceninja-mcp/blob/main/LICENSE). Modified versions, including ones run as a network service, must publish their source.
