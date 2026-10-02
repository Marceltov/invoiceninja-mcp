# Connecting clients

A client can authenticate in two ways: with an InvoiceNinja API token it sends itself, or with OAuth, where it only needs the URL and you log in in the browser. OAuth needs the server configured for it, see [Configuration](configuration.md#oauth).

## With an API token

Create a token in InvoiceNinja (*Settings → Account Management → Integrations → API tokens*) and give it to the client in the `Authorization` header.

```bash
claude mcp add invoiceninja --scope user --transport http \
  https://your-host/mcp \
  --header "Authorization: YOUR_INVOICENINJA_API_TOKEN"
```

This works in the `token` and `both` auth modes. In `both`, any bearer that does not start with `inmcp_` is forwarded to InvoiceNinja as a raw token.

## With OAuth

Add the URL with no header:

```bash
claude mcp add --transport http invoiceninja https://your-host/mcp
```

Then authenticate in the browser (in Claude Code run `/mcp` and pick the server). Claude Desktop, claude.ai and ChatGPT work the same way: add `https://your-host/mcp` as a custom connector and complete the login when prompted.

The login page asks for:

* **Email** and **password** of your InvoiceNinja account.
* **2FA code**, only if your account has it enabled. This field exists but 2FA login was not tested against a real instance.

!!! note "Accounts without a password login"
    Passkey-only and SSO-only accounts have no password to enter and are untested.

The page also shows the client's name and the host it will return to. Only continue if you started the connection yourself.

## Where the token lives

After login the server mints an API token named `MCP: <client name>` for that client. Find it under *Settings → Account Management → Integrations → API tokens*. Your password is not stored, and the login never logs out your other sessions.

## Revoking access

Remove the connector in the client, which revokes its token. You can also delete the `MCP: <client name>` token in InvoiceNinja. Either way the token stops working at once.

InvoiceNinja only archives a deleted token. Archived tokens are rejected, but the archived `MCP: <client name>` entries stay in the list.
