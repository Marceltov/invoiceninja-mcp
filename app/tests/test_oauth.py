import asyncio

import pytest

import server

OAUTH_VARS = {"MCP_BASE_URL": "https://mcp.example", "MCP_OAUTH_SECRET": "s3cret-s3cret"}


@pytest.mark.parametrize(
    "mode, env, expected",
    [
        (None, {}, "token"),          # unset + no OAuth vars: safe upgrade path
        (None, OAUTH_VARS, "both"),   # unset + vars: default is both
        ("token", {}, "token"),
        ("oauth", OAUTH_VARS, "oauth"),
        ("BOTH", OAUTH_VARS, "both"),
    ],
)
def test_resolve_auth_mode(monkeypatch, mode, env, expected):
    for var in ("MCP_AUTH_MODE", *OAUTH_VARS):
        monkeypatch.delenv(var, raising=False)
    if mode:
        monkeypatch.setenv("MCP_AUTH_MODE", mode)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert server.resolve_auth_mode() == expected


@pytest.mark.parametrize("mode", ["oauth", "both", "bogus"])
def test_resolve_auth_mode_fails_loudly_when_explicit(monkeypatch, mode):
    for var in OAUTH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MCP_AUTH_MODE", mode)
    with pytest.raises(RuntimeError, match="MCP_"):
        server.resolve_auth_mode()


def test_plain_http_base_url_fails_at_startup(monkeypatch):
    monkeypatch.setenv("MCP_BASE_URL", "http://192.168.1.50:8081")
    with pytest.raises(RuntimeError, match="MCP_BASE_URL.*HTTPS"):
        server.validate_base_url()


def test_localhost_http_base_url_is_allowed(monkeypatch):
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8081/")
    assert server.validate_base_url() == "http://localhost:8081"


def test_encrypted_store_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "OAUTH_STORE_DIR", tmp_path)
    monkeypatch.setenv("MCP_OAUTH_SECRET", "s3cret-s3cret")
    store = server.build_oauth_store()

    async def go():
        await store.put("k", {"secret": "needle-value"}, collection="c")
        return await store.get("k", collection="c")

    assert asyncio.run(go()) == {"secret": "needle-value"}
    stored = b"".join(f.read_bytes() for f in tmp_path.rglob("*") if f.is_file())
    assert stored and b"needle-value" not in stored  # encrypted at rest
