---
status: accepted
date: 2026-10-02
decision-makers: Marcel Bruckner
---

# OAuth login via the InvoiceNinja email and password

## Context and Problem Statement

App and remote MCP clients (claude.ai, ChatGPT, IDEs) expect the MCP authorization spec (2026-07-28): connect with only a URL, then authenticate in the browser. Until now the server only forwarded a raw API token from the `Authorization` header. InvoiceNinja has no OAuth of its own, so to offer OAuth this server has to be its own authorization server and must end up holding an InvoiceNinja API token it can forward as `X-API-TOKEN`. How should a user prove who they are, and where does that token come from?

## Considered Options

* Own authorization server with an InvoiceNinja email and password login that mints a per-client API token
* Own authorization server with a paste-an-existing-token login
* Delegate to an external identity provider
* Stay token-only

## Decision Outcome

Chosen option: "Own authorization server with an InvoiceNinja email and password login", because the user already has exactly these credentials and the server can turn them into a named, revocable token without any extra system. The login page (`/login`) sends the credentials to InvoiceNinja `POST /api/v1/login`, then uses the returned session token to call `POST /api/v1/tokens` and mint a token named `MCP: <client name>`. Only that minted token is kept. Paste-a-token login was rejected as a worse experience that moves the work back to the user, an external IdP as an extra moving part with no way to map its identities to InvoiceNinja users, and staying token-only leaves app clients unsupported.

`MCP_AUTH_MODE` selects `token` (unchanged behaviour), `oauth`, or `both`. Unset means `both` when `MCP_BASE_URL` and `MCP_OAUTH_SECRET` are set, else `token`. OAuth bearers carry the prefix `inmcp_`; in `both` mode any other bearer is forwarded as a raw InvoiceNinja token, so existing token clients keep working.

### Consequences

* Good, because a client connects with only the MCP URL.
* Good, because every client gets its own named token (`MCP: <client name>`) that can be revoked on its own, and the user's password is never stored or logged.
* Good, because token clients are unaffected.
* Bad, because the server is no longer stateless in OAuth modes: it needs a volume at `/data` and `MCP_OAUTH_SECRET` to keep registrations and tokens encrypted at rest.
* Bad, because the issuer must be HTTPS (localhost excepted), so remote OAuth needs a TLS proxy.
* Bad, because accounts that need 2FA, passkeys or SSO may not be able to use `/login`. The form has an optional 2FA code field, but 2FA login and passkey or SSO-only accounts were not verified against a real instance and are untested.
* Bad, because Dynamic Client Registration lets anyone send a user a genuine login link. The only mitigation is that the page shows the client name and redirect host; there is no allowlist.
* Bad, because a client that never returns leaves its named token in InvoiceNinja until someone deletes it.
* Bad, because InvoiceNinja `DELETE /api/v1/tokens/{id}` only archives a token. Archived tokens are rejected, so revocation works, but archived `MCP: <client>` entries stay listed under *Settings → Account Management → Integrations → API tokens*.
* Bad (hand-rolled clients only), because the MCP SDK's `/revoke` endpoint requires a `client_secret` form field even for public clients. An empty string works; real MCP clients already handle this.

## Spec alignment

* Dynamic Client Registration is deprecated but still allowed in 2026-07-28, and it is what claude.ai and ChatGPT use, so it is the registration method here.
* Client ID Metadata Documents are not supported because the MCP SDK has no server support for them. Adding them is a follow-up.
* Access tokens are bound to the `resource` from the authorization request, and tokens whose resource is not this server's canonical URI are rejected (audience binding).
* The `iss` parameter (RFC 9207) is sent on the authorization redirect and advertised as `authorization_response_iss_parameter_supported` in the authorization-server metadata.
* PKCE is required, access tokens live 1 hour, refresh tokens 30 days with rotation, authorization codes 5 minutes.

## Probe findings

Checked against InvoiceNinja 5.13.43:

* `POST /api/v1/login` returns the user's company users, each with a shared system token named "User Token" that is reused on every login. The server must therefore never call `/logout` with it: that would delete the token the user's other sessions rely on. The session token is only used once to mint the per-client token and is then discarded.
* `POST /api/v1/tokens` with that session token returns a normal, non-system token that keeps working after the session token is gone.
* Revocation (the OAuth `/revoke` endpoint) drops the OAuth pair and makes a best-effort `DELETE /api/v1/tokens/{id}`, which archives the minted token.

## Confirmation

Unit tests in `app/tests/test_oauth.py` (mock transport) cover the full flow, mode selection, refresh rotation, revoke, raw pass-through in `both`, audience rejection, bad password, replayed login form and the upload route. `app/tests/live/test_oauth_flow.py` runs the flow against the dev stack, including revoking the minted token and asserting the 401 afterwards.
