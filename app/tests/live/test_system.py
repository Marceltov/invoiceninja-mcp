from tests.live._client import client, run_async


def test_connects_and_lists_generated_tools():
    async def run():
        async with client() as c:
            return await c.list_tools()
    tools = run_async(run())
    names = {t.name for t in tools}
    assert len(tools) >= 300
    assert "getClients" in names
    assert "storeInvoice" in names
    assert "login" not in names
    assert "logout" not in names
