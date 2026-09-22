import base64

from tests.live._client import client, run_async


async def _make_client(c) -> str:
    created = await c.call_tool("storeClient", {
        "name": "itest-upload-client",
        "country_id": "840",
        "contacts": [{"first_name": "Itest", "email": "itest-upload@example.com"}],
    })
    return created.structured_content["data"]["id"]


def test_upload_client_document_is_actually_retrievable():
    content = b"hello from invoiceninja-mcp test\n"

    async def run():
        async with client() as c:
            client_id = await _make_client(c)
            await c.call_tool("uploadClient", {
                "id": client_id,
                "filename": "itest-note.txt",
                "content_base64": base64.b64encode(content).decode(),
            })
            # Client.documents isn't populated by default -- the spec's
            # client_include param documents `?include=documents` as the way
            # to get a client's uploaded documents back on showClient.
            got = await c.call_tool("showClient", {"id": client_id, "include": "documents"})
            return got.structured_content
    data = run_async(run())
    documents = data["data"].get("documents", [])
    # Review Focus: assert the upload actually persisted, not just that the
    # call returned 200 -- a broken multipart encoding can silently drop the
    # file server-side while InvoiceNinja still answers 200.
    assert any(doc.get("name") == "itest-note.txt" for doc in documents), (
        f"uploaded document not found in client.documents: {documents}"
    )
