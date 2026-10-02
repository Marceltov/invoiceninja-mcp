# Security

!!! warning
    The MCP endpoint grants **full access to your InvoiceNinja account** — whatever the token's permissions allow.

- Every request to the MCP path must carry an API token in the `Authorization` header (an optional `Bearer ` prefix is stripped). Requests without one are rejected with `401` before reaching any tool.
- The server never validates the token itself and holds no secret; InvoiceNinja enforces validity when the forwarded call arrives.
- `/health` is always unauthenticated (used by the container healthcheck).
- The token is sent to InvoiceNinja as `X-API-TOKEN` on every call. Over plain HTTP it travels in cleartext, so keep traffic on a **trusted network** (LAN or Docker network) or put TLS in front.
- By default any `Host` is accepted (DNS-rebinding protection off) so the server works behind a proxy or by LAN IP. Set `MCP_ALLOWED_HOSTS` to the host[:port] values you actually use to lock this down.
- Use a token scoped to what the client needs — the token's permissions are the only access boundary.
