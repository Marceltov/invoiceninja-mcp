"""Skip the live/ integration tests when the docker-compose stack is down."""

import os

import pytest

from tests.live._client import MCP_URL, stack_reachable


def pytest_collection_modifyitems(config, items):
    if stack_reachable():
        return
    skip = pytest.mark.skip(
        reason=f"live stack not reachable at {MCP_URL} (run `docker compose up -d --wait`)"
    )
    for item in items:
        path = str(item.fspath).replace(os.sep, "/")
        if "/tests/live/" in path:
            item.add_marker(skip)
