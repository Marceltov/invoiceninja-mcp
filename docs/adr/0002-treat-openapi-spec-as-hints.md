---
status: accepted
date: 2026-09-27
decision-makers: Marcel Bruckner
---

# Treat the OpenAPI spec as hints, with a raw apiRequest escape hatch

## Context and Problem Statement

Tools are generated from InvoiceNinja's published OpenAPI spec, which turned out to be wrong in many ways found only by hitting them: missing request bodies, wrong types (`country_id`, `Design.design`), over-strict `required` lists (`updateQuote` demanding `due_date`), and fields missing from write bodies (`design_id`). FastMCP enforces the spec strictly on both inputs and outputs and silently drops undeclared arguments, so each gap needed its own patch. Should we keep generating tools, and how do we stop patching gap by gap?

## Considered Options

* Keep generating, patch each spec bug as it's found
* Replace generation with hand-written tools only
* Keep generating, but treat the spec as hints and add a raw pass-through tool

## Decision Outcome

Chosen option: "Treat the spec as hints and add a raw pass-through", because it ends the patch-per-bug loop while keeping generation's breadth (~378 tools, following the instance version per ADR 0001). InvoiceNinja validates every request itself, so the spec's input/output constraints add failure modes without adding safety: body `required` lists are dropped, output validation is off, and `apiRequest` reaches any `/api/v1/` endpoint with the caller's token (no more access than the generated tools). Hand-writing everything was rejected as too much upkeep for rarely used endpoints; hand-written tools remain the plan for high-value or dangerous workflows (e.g. company settings, whose update replaces unspecified settings with defaults).

### Consequences

* Good, because spec errors no longer block calls; InvoiceNinja's validation messages come back instead.
* Good, because missing fields/endpoints are reachable immediately via `apiRequest`, without a release.
* Bad, because generated tool schemas no longer tell the caller which body fields are mandatory; they learn it from the API's 422 response.
* Bad, because `apiRequest` is untyped — callers must know the endpoint shape.
