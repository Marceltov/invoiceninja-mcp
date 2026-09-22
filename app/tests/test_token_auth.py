import httpx
import pytest

import server


def _run_auth(header_value):
    reset = server._incoming_auth.set(header_value)
    try:
        auth = server.InvoiceNinjaTokenAuth()
        request = httpx.Request("GET", "http://invoiceninja:80/api/v1/clients")
        return next(auth.auth_flow(request))
    finally:
        server._incoming_auth.reset(reset)


def test_strips_bearer_prefix():
    out = _run_auth("Bearer secret-token")
    assert out.headers["X-API-TOKEN"] == "secret-token"


def test_bearer_prefix_case_insensitive():
    out = _run_auth("bearer secret-token")
    assert out.headers["X-API-TOKEN"] == "secret-token"


def test_raw_token_passthrough():
    out = _run_auth("raw-token-no-prefix")
    assert out.headers["X-API-TOKEN"] == "raw-token-no-prefix"


def test_sets_requested_with_header():
    out = _run_auth("raw-token-no-prefix")
    assert out.headers["X-Requested-With"] == "XMLHttpRequest"


def test_missing_token_raises():
    reset = server._incoming_auth.set(None)
    try:
        auth = server.InvoiceNinjaTokenAuth()
        request = httpx.Request("GET", "http://invoiceninja:80/api/v1/clients")
        with pytest.raises(RuntimeError):
            next(auth.auth_flow(request))
    finally:
        server._incoming_auth.reset(reset)


def test_empty_after_bearer_strip_raises():
    reset = server._incoming_auth.set("Bearer ")
    try:
        with pytest.raises(RuntimeError):
            next(server.InvoiceNinjaTokenAuth().auth_flow(
                httpx.Request("GET", "http://invoiceninja:80/api/v1/clients")))
    finally:
        server._incoming_auth.reset(reset)


def test_bearer_with_only_whitespace_raises():
    reset = server._incoming_auth.set("Bearer     ")
    try:
        with pytest.raises(RuntimeError):
            next(server.InvoiceNinjaTokenAuth().auth_flow(
                httpx.Request("GET", "http://invoiceninja:80/api/v1/clients")))
    finally:
        server._incoming_auth.reset(reset)
