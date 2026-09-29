import base64

from tests.live._client import client, run_async


def test_api_request_downloads_an_invoice_pdf():
    async def run():
        async with client() as c:
            created = await c.call_tool("storeClient", {
                "name": "itest-pdf-client",
                "country_id": "840",
                "contacts": [{"first_name": "Itest", "email": "itest-pdf@example.com"}],
            })
            client_id = created.structured_content["data"]["id"]
            invoice = await c.call_tool("storeInvoice", {"client_id": client_id})
            invoice_id = invoice.structured_content["data"]["id"]
            return await c.call_tool("apiRequest", {
                "method": "GET", "path": f"/api/v1/invoices/{invoice_id}/download",
            })
    result = run_async(run())
    assert result.structured_content["status"] == 200
    assert result.structured_content["content_type"] == "application/pdf"
    # A real, rendered PDF -- not JSON or an error page mislabelled as one.
    assert base64.b64decode(result.content[1].resource.blob).startswith(b"%PDF")
