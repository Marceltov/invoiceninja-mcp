# Security

!!! warning
    The MCP endpoint grants **full access to your InvoiceNinja account** — whatever the token's permissions allow.

- Every request to the MCP path must carry an API token in the `Authorization` header (an optional `Bearer ` prefix is stripped). Requests without one are rejected with `401` before reaching any tool.
- The server never validates an API token itself; InvoiceNinja enforces validity when the forwarded call arrives.
- In `token` mode the server is stateless and holds no secret. In `oauth` and `both` modes it stores client registrations and the tokens it minted, encrypted at rest with `MCP_OAUTH_SECRET` under `/data/oauth`.
- In `both` mode a bearer without the `inmcp_` prefix is forwarded as a raw InvoiceNinja token, exactly as in `token` mode.
- `/health` is always unauthenticated (used by the container healthcheck).
- The token is sent to InvoiceNinja as `X-API-TOKEN` on every call. Over plain HTTP it travels in cleartext, so keep traffic on a **trusted network** (LAN or Docker network) or put TLS in front.
- By default any `Host` is accepted (DNS-rebinding protection off) so the server works behind a proxy or by LAN IP. Set `MCP_ALLOWED_HOSTS` to the host[:port] values you actually use to lock this down.
- Use a token scoped to what the client needs — the token's permissions are the only access boundary.

## OAuth login

- The login page sends your email and password straight to InvoiceNinja and never stores or logs them. It uses the resulting session token once to mint a token named `MCP: <client name>`, then discards it without logging you out.
- Each client gets its own token, revocable on its own. See [Connecting clients](connecting.md#revoking-access) for the archived-token caveat.
- Remote OAuth needs an HTTPS `MCP_BASE_URL` (localhost excepted), so put TLS in front. Access tokens bound to another resource are rejected here (audience check).
- Dynamic Client Registration lets anyone register a client and send you a genuine login link. The page shows the client name and redirect host, but there is no allowlist: only log in to connections you started.
- The plain-HTTP `POST /upload/{entity}/{id}` route is gated in `oauth` and `both` modes by `UploadAuthMiddleware`, which verifies the bearer itself and forwards the minted token. Requests without a valid one get `401`.
- Keep `MCP_OAUTH_SECRET` private and the `/data` volume backed up. Losing or changing the secret logs every client out.
