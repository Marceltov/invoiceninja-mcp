#!/usr/bin/env python3
"""Mint a dev API token for the local InvoiceNinja fixture and write it to
invoiceninja.token (gitignored). Run after `docker compose up -d --wait`; see
run-tests.sh."""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

APP_URL = "http://localhost:8082"
EMAIL = "admin@example.com"
PASSWORD = "invoiceninja-mcp-dev"
TOKEN_FILE = Path(__file__).resolve().parent.parent / "invoiceninja.token"


def main() -> int:
    body = json.dumps({"email": EMAIL, "password": PASSWORD}).encode()
    req = urllib.request.Request(
        f"{APP_URL}/api/v1/login",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"Login failed: {e.code} {e.read().decode()}", file=sys.stderr)
        return 1

    # InvoiceNinja's /login wraps the result in a top-level "data" envelope
    # (like the rest of its API), containing either a single CompanyUser
    # object or a list of them (multi-company accounts); handle both.
    data = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
    entry = data[0] if isinstance(data, list) else data
    token = entry["token"]["token"]
    TOKEN_FILE.write_text(token + "\n")
    print(f"Wrote dev token to {TOKEN_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
