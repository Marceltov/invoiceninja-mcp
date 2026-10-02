# Tools

## Generated tools

Each documented API operation becomes a tool named after its `operationId`: `getClients`, `storeClient`, `showInvoice`, `updateQuote`, `deleteExpense`, `getARSummaryReport`, and so on. Only `login` and `logout` are excluded — they manage session tokens, and an MCP client already authenticates via the header (logout would invalidate its own credential).

InvoiceNinja's published spec is inaccurate in places, so it is treated as a **hint**: request bodies require nothing up front and responses aren't validated. InvoiceNinja's own validation errors come back instead. See [ADR 0002](adr/0002-treat-openapi-spec-as-hints.md).

## `apiRequest`

The escape hatch for fields or endpoints a generated tool can't express. Undeclared arguments are silently dropped by generated tools, so use this when a field is missing from the spec.

| Argument | Description |
| --- | --- |
| `method` | HTTP method (`GET`, `POST`, `PUT`, `DELETE`, …). |
| `path` | Must start with `/api/v1/` (e.g. `/api/v1/quotes/{id}`). |
| `query` | Optional query-string object. |
| `body` | Optional JSON object or array. |

It returns `{"status", "body"}`; a 4xx with InvoiceNinja's validation message is returned, not raised, so the call can be corrected. Binary responses such as `/api/v1/quotes/{id}/download` PDFs come back as an embedded file. It is limited to `/api/v1/` on the configured instance with the caller's own token — no more access than the generated tools.

## `uploadClient`

The spec documents `POST /clients/{id}/upload` with binary multipart parts, which JSON-in/JSON-out tools can't express — and its method is wrong (InvoiceNinja registers it as PUT, and PHP only parses multipart on POST). The replacement tool takes base64 content and sends a POST with a `_method: "PUT"` field.

| Argument | Description |
| --- | --- |
| `id` | Client id. |
| `filename` | Name to store the file under; the type is inferred from the extension. |
| `content_base64` | Base64-encoded file contents. |

## File upload route: `POST /upload/{entity}/{id}`

A plain HTTP endpoint (not an MCP tool) for attaching files to any entity that has a `/{id}/upload` route in the spec: `expenses`, `invoices`, `quotes`, `payments`, `vendors`, … Base64 inside a tool call is impractical for a receipt photo or PDF, so send the bytes directly. It needs the same `Authorization` header as the MCP endpoint, is served next to it (same host and port), and forwards the files to InvoiceNinja the way `uploadClient` does.

```bash
curl -X POST "http://localhost:8081/upload/expenses/EXPENSE_ID" \
  -H "Authorization: YOUR_INVOICENINJA_API_TOKEN" \
  -F "documents=@receipt.pdf"
```

Several `documents` parts attach several files. It returns InvoiceNinja's response; an unknown entity answers `404`, no file `400`, a missing header `401`.
