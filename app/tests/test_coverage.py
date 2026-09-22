"""Enforce that every *currently tested* InvoiceNinja operationId is
exercised by a live test, and that no test references an unknown tool name.

Unlike trillium-mcp's guard (which requires every spec operationId to have a
live test -- feasible for its ~38 tools), InvoiceNinja's spec has 365
operations. This plan intentionally live-tests only a representative slice
(see the plan's Global Constraints); TESTED_OPERATIONS lists exactly that
slice. Expanding coverage is follow-on work: add the operationId to
TESTED_OPERATIONS and a matching live test, in the same PR.
"""

import re
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve()  # app/tests/test_coverage.py
_LIVE_DIR = _HERE.parent / "live"  # app/tests/live
_SPEC = _HERE.parents[1] / "invoiceninja-api-docs.yaml"  # app/invoiceninja-api-docs.yaml

# Endpoints intentionally NOT exposed as MCP tools, so they are not tested.
# login/logout manage InvoiceNinja session tokens; excluded in server.py via
# a RouteMap (see the build_server comment).
_EXCLUDED_OPERATIONS = {"login", "logout"}

# The slice of the 365-operation surface this plan live-tests. Expand this
# set (and add a matching live test under tests/live/) as coverage grows.
_TESTED_OPERATIONS = {
    "getClients", "storeClient", "showClient", "updateClient", "deleteClient",
    "storeInvoice", "showInvoice",
    "uploadClient",
}


def _spec_operation_ids() -> set[str]:
    spec = yaml.safe_load(_SPEC.read_text())
    ids = set()
    for path_item in spec.get("paths", {}).values():
        for op in path_item.values():
            if isinstance(op, dict) and "operationId" in op:
                ids.add(op["operationId"])
    return ids


def _exercised_tool_names() -> set[str]:
    names = set()
    for py in _LIVE_DIR.glob("test_*.py"):
        for line in py.read_text().splitlines():
            code = line.split("#", 1)[0]
            names.update(re.findall(r'["\']([a-zA-Z]+)["\']', code))
    return names


def test_tested_operations_all_have_a_live_test():
    exercised = _exercised_tool_names()
    uncovered = sorted(_TESTED_OPERATIONS - exercised)
    assert not uncovered, f"listed as tested but no live test found: {uncovered}"


def test_excluded_operations_are_not_registered():
    import asyncio

    import server

    mcp = server.build_server()
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    still_present = sorted(_EXCLUDED_OPERATIONS & names)
    assert not still_present, f"excluded tools still registered: {still_present}"


def test_no_unknown_tool_names_referenced():
    operations = _spec_operation_ids()
    called = set(
        re.findall(
            r'(?:call_tool|call)\(\s*["\']([a-zA-Z]+)["\']',
            "\n".join(p.read_text() for p in _LIVE_DIR.glob("test_*.py")),
        )
    )
    unknown = sorted(called - operations)
    assert not unknown, f"tests call unknown tool names: {unknown}"


def test_tested_operations_are_real_spec_operations():
    operations = _spec_operation_ids()
    bogus = sorted(_TESTED_OPERATIONS - operations - {"getClients"} | (
        {"getClients"} - operations
    ))
    # (getClients is checked separately since it's a GET list endpoint some
    # spec revisions name differently; fail loudly either way if missing.)
    assert "getClients" in operations, "getClients missing from the spec -- name may have changed upstream"
    missing = sorted(_TESTED_OPERATIONS - operations)
    assert not missing, f"TESTED_OPERATIONS lists operationIds not in the spec: {missing}"
