from tests.live._client import client, run_async


def _client_payload(name: str) -> dict:
    return {
        "name": name,
        "country_id": "840",  # USA
        "contacts": [{"first_name": "Itest", "email": "itest@example.com"}],
    }


def test_create_and_get_client():
    async def run():
        async with client() as c:
            created = await c.call_tool("storeClient", _client_payload("itest-create"))
            client_id = created.structured_content["data"]["id"]
            got = await c.call_tool("showClient", {"id": client_id})
            return client_id, got.structured_content
    client_id, data = run_async(run())
    assert data["data"]["id"] == client_id
    assert data["data"]["name"] == "itest-create"


def test_update_client_name():
    async def run():
        async with client() as c:
            created = await c.call_tool("storeClient", _client_payload("itest-before"))
            client_id = created.structured_content["data"]["id"]
            payload = _client_payload("itest-after")
            # updateClient's generated tool disambiguates the path id from the
            # (readOnly) body "id" field in ClientRequest by suffixing the path
            # one "__path" -- plain "id" here is read as the body field and
            # left blank, so the path segment ends up empty and InvoiceNinja
            # 400s with "No query results for model [App\Models\Client]".
            await c.call_tool("updateClient", {"id__path": client_id, **payload})
            got = await c.call_tool("showClient", {"id": client_id})
            return got.structured_content
    data = run_async(run())
    assert data["data"]["name"] == "itest-after"


def test_delete_client_marks_it_deleted():
    async def run():
        async with client() as c:
            created = await c.call_tool("storeClient", _client_payload("itest-delete"))
            client_id = created.structured_content["data"]["id"]
            await c.call_tool("deleteClient", {"id": client_id})
            got = await c.call_tool("showClient", {"id": client_id})
            return got.structured_content
    data = run_async(run())
    # InvoiceNinja soft-deletes: the record is still fetchable but flagged.
    assert data["data"]["is_deleted"] is True
