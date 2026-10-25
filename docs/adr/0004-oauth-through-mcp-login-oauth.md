---
status: accepted
date: 2026-10-25
decision-makers: Marcel Bruckner
---

# OAuth through the shared mcp-login-oauth library, on FastMCP 4

## Context and Problem Statement

ADR 0003 made this server its own OAuth authorization server, with the InvoiceNinja email, password
and 2FA code as the login and one named API token minted per client. The code (provider, login and
dead-end pages, encrypted store, `IssuerFlagMiddleware`) was one of four drifting copies across
Marcel's MCP servers. This copy had no delay after a wrong password, and its `/revoke` was the MCP
SDK's, which demands a client secret, so the live test had to send an empty one. The library also
needs FastMCP 4, and this server was on FastMCP 3.

## Considered Options

* Keep the in-repo copy and port fixes by hand.
* Use [mcp-login-oauth](https://github.com/Marceltov/mcp-login-oauth), supplying only the login
  fields, the login check that mints the named token, the token deletion on revoke and the raw-token
  passthrough.

## Decision Outcome

Chosen: **mcp-login-oauth** (v0.2.0, pinned by git tag). ADR 0003 still holds: InvoiceNinja checks
the login, every client gets its own token named `MCP: <client name>`, and revoking deletes that
token, never `/logout`. v0.2.0 of the library was cut for this server: `check_login` learns the
client's name, and the library took over RFC 8707 resource binding and RFC 9207 `iss`, which this
server had built itself.

FastMCP 4 comes with it. Its OpenAPI provider deprecates plain `httpx` clients for `httpx2` (the same
API), so the server uses `httpx2`; the generated tools and route maps are unchanged.

### Consequences

* Good, because the server gets public-client revoke, the failed-login delay and login links that end
  after 5 failures, and loses ~350 lines.
* Bad, because stored OAuth logins don't carry over: the store has a new salt, so old entries (another
  record shape) read as misses and OAuth clients log in once more. Their old named tokens stay in
  InvoiceNinja until deleted there (Settings → API tokens).
* Bad, because an unreachable InvoiceNinja now shows the library's generic "Could not reach
  InvoiceNinja" (HTTP 502) instead of the exception's class name.
* Bad, because the image build needs git in its builder stage until the library is on PyPI.
