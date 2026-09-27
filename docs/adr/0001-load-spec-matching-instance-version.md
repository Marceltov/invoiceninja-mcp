---
status: accepted
date: 2026-09-27
decision-makers: Marcel Bruckner
---

# Load the OpenAPI spec matching the running InvoiceNinja version

## Context and Problem Statement

Tools are generated from an OpenAPI spec. A single bundled spec only matches one InvoiceNinja release: users on other versions get tools for endpoints their instance lacks, or miss new ones (5.13.43 has 379 operations vs. 365 in the originally bundled spec, with some renamed). As an open-source sidecar, the server will run against many versions. How should it pick the spec?

## Considered Options

* Bundled spec only, with the tested version documented
* Share the instance's own spec file (`/var/www/html/openapi/api-docs.yaml`) with the sidecar via a named volume
* Detect the instance version and download that release's spec from GitHub, falling back to the bundled spec

## Decision Outcome

Chosen option: "Detect the version and download the matching spec", because it's the only one that follows the instance automatically with zero setup. InvoiceNinja sends `X-APP-VERSION` even on an unauthenticated ping, and the spec at each release tag is byte-identical to the one baked into that image.

The volume option was rejected: Docker seeds a named volume from the image only once, so after the first InvoiceNinja upgrade the sidecar would silently keep the old spec. Keeping it fresh needs a custom entrypoint in someone else's image.

### Consequences

* Good, because every user gets tools for exactly their version, including after upgrades (on sidecar restart).
* Good, because it degrades gracefully: any failure falls back to the bundled spec with a logged warning, and `INVOICENINJA_API_SPEC` pins a file for offline setups.
* Bad, because startup now depends on GitHub and trusts the upstream repo's spec at a pinned tag. The spec only shapes tool definitions; requests still go solely to the configured instance.
* Bad, because production may run a different spec than unit tests (which always use the bundled file). The in-memory spec patches are written to be no-ops where upstream is correct, so they stay safe across versions.
