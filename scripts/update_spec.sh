#!/usr/bin/env bash
# Refresh the bundled OpenAPI spec (the offline fallback) to a given
# InvoiceNinja release, e.g.: scripts/update_spec.sh 5.13.43
set -euo pipefail
version="${1:?usage: $0 <invoiceninja-version>}"
cd "$(dirname "$0")/.."
curl -fsSL "https://raw.githubusercontent.com/invoiceninja/invoiceninja/v${version}/openapi/api-docs.yaml" \
  -o app/invoiceninja-api-docs.yaml
echo "Bundled spec updated to InvoiceNinja ${version}."
