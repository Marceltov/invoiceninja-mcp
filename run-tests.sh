#!/usr/bin/env bash
#
# Run the test suite against a clean, disposable InvoiceNinja fixture.
#
# Flow: stop containers -> wipe fixture volumes -> (re)build & start
# containers, blocking until every healthcheck passes -> mint a fresh dev
# token -> run pytest -> stop containers -> wipe fixture volumes again.
#
# Unlike trillium-mcp's bind-mounted, git-resettable fixture, this stack uses
# named Docker volumes (app/db data isn't tracked in this repo), so "reset"
# means removing the volumes and letting first-boot re-seed the admin
# company/user (see docker-compose.yaml's IN_USER_EMAIL/IN_PASSWORD).
#
# Any extra arguments are passed through to pytest, e.g.:
#   ./run-tests.sh                       # full suite
#   ./run-tests.sh tests/live -q         # just the live tests, quietly
#   ./run-tests.sh -k clients            # a single test by keyword
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

cleanup() {
  echo "== Teardown: stopping containers and wiping fixture volumes =="
  docker compose down -v
  rm -f invoiceninja.token
}
trap cleanup EXIT

echo "== Stopping containers and wiping fixture volumes =="
docker compose down -v
rm -f invoiceninja.token

echo "== Building and starting containers (blocks until all healthchecks pass) =="
docker compose up -d --build --wait

echo "== Minting a dev API token =="
python3 scripts/mint_dev_token.py

echo "== Running tests =="
( cd app && uv run pytest "$@" )
