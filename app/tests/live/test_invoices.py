from tests.live._client import client, run_async


async def _make_client(c) -> str:
    created = await c.call_tool("storeClient", {
        "name": "itest-invoice-client",
        "country_id": "840",
        "contacts": [{"first_name": "Itest", "email": "itest-inv@example.com"}],
    })
    return created.structured_content["data"]["id"]


def test_create_and_get_invoice():
    async def run():
        async with client() as c:
            client_id = await _make_client(c)
            created = await c.call_tool("storeInvoice", {"client_id": client_id})
            invoice_id = created.structured_content["data"]["id"]
            got = await c.call_tool("showInvoice", {"id": invoice_id})
            return client_id, invoice_id, got.structured_content
    client_id, invoice_id, data = run_async(run())
    assert data["data"]["id"] == invoice_id
    assert data["data"]["client_id"] == client_id
