# Contributing

- [Local development environment](#local-development-environment)
  - [Dev fixture credentials](#dev-fixture-credentials)
  - [Talking to the MCP server](#talking-to-the-mcp-server)
  - [Resetting the fixture](#resetting-the-fixture)
- [Running the tests](#running-the-tests)
  - [The easy way: run-tests.sh](#the-easy-way-run-testssh)
  - [Manually](#manually)
- [Tools generated from the OpenAPI spec](#tools-generated-from-the-openapi-spec)

## Local development environment

The repo ships a ready-to-run dev stack: a throwaway InvoiceNinja instance (app + nginx + mysql + redis), plus the MCP server built from local source.

```bash
docker compose up -d --build --wait
python3 scripts/mint_dev_token.py
```

This starts five services (see [`docker-compose.yaml`](docker-compose.yaml)):

| Service | URL | Notes |
| --- | --- | --- |
| `invoiceninja` (nginx) | http://localhost:8082 | Web UI + API (`/api/v1`) |
| `app` | (internal only) | PHP-FPM, InvoiceNinja itself |
| `mysql` / `redis` | (internal only) | App datastores |
| `invoiceninja-mcp` | http://localhost:8081/mcp | MCP endpoint (built from local `Dockerfile`) |

The `invoiceninja-mcp` service uses `build: .`, so it always runs your local code. After changing anything under `app/`, rebuild with `docker compose up -d --build --wait`.

Check the MCP server is up:

```bash
curl http://localhost:8081/health   # -> ok
```

### Dev fixture credentials

All **committed dev fixture** values, scoped to this disposable instance only — do not reuse them anywhere real.

- **InvoiceNinja web UI:** http://localhost:8082, log in with `admin@example.com` / `invoiceninja-mcp-dev`.
- **API token:** not committed (unlike trillium-mcp's `etapi.token`) — a fresh container boot creates a new admin user, so the token can't be static. Run `python3 scripts/mint_dev_token.py` after bringing the stack up; it writes to [`invoiceninja.token`](invoiceninja.token) (gitignored).

### Talking to the MCP server

Point any MCP client at the endpoint with the dev token:

```bash
claude mcp add invoiceninja-dev --transport http \
  http://localhost:8081/mcp \
  --header "Authorization: $(cat invoiceninja.token)"
```

### Resetting the fixture

This stack uses named Docker volumes, not a bind-mounted, git-resettable directory like trillium-mcp's `trilium-data/`. To discard all local data and return to a pristine fixture:

```bash
docker compose down -v
docker compose up -d --wait
python3 scripts/mint_dev_token.py
```

## Running the tests

Tests live in `app/tests/` and come in two kinds:

- **Unit tests** (`app/tests/test_*.py`) run against a mock InvoiceNinja transport — no running InvoiceNinja required.
- **Live integration tests** (`app/tests/live/`) drive the real MCP server over HTTP against the running stack. They **auto-skip** when the MCP `/health` endpoint is unreachable.

### The easy way: `run-tests.sh`

[`run-tests.sh`](run-tests.sh) runs the whole suite against a **clean, disposable fixture**. It stops the containers, wipes the fixture volumes, rebuilds and starts them (blocking until every healthcheck passes), mints a fresh dev token, runs `pytest`, then stops the containers and wipes the volumes again — even if a test fails. Extra arguments pass through to pytest:

```bash
./run-tests.sh                  # full suite against a fresh fixture
./run-tests.sh tests/live -q    # just the live tests, quietly
./run-tests.sh -k clients       # a single test by keyword
```

It leaves the stack **stopped** at the end; run `docker compose up -d --wait` to bring it back for interactive work.

### Manually

```bash
cd app
uv run pytest        # or: .venv/bin/python -m pytest
```

Without the stack up, the live tests skip and the unit tests still run.

## Tools generated from the OpenAPI spec

Most MCP tools are generated automatically from [`app/invoiceninja-api-docs.yaml`](app/invoiceninja-api-docs.yaml) via `FastMCP.from_openapi`. `login`/`logout` are excluded outright, and `uploadClient` is excluded and replaced with a hand-written tool that base64-decodes file content and sends it as multipart form data with a `_method: "PUT"` field, matching InvoiceNinja's actual (PUT, not POST as the spec claims) route for that endpoint (see `app/server.py`). If you special-case another endpoint, cover it with a live test under `app/tests/live/` and add its operationId to `app/tests/test_coverage.py`'s `_TESTED_OPERATIONS`.
