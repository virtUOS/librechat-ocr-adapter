"""Replay of LibreChat v0.8.8 `uploadMistralOCR` (packages/api/src/files/mistral/crud.ts).

Step 1 uploadDocumentToMistral: multipart, fields `purpose=ocr` and `file` with the
        original filename; reads `id`.
Step 2 getSignedUrl: GET /files/{id}/url?expiry=24; reads `url`.
Step 3 performOCR: JSON {model, image_limit: 0, include_image_base64: false,
        document: {type, document_url | image_url}}; `image_url` for image/* or
        png/jpg/gif/bmp/webp/tif, else `document_url`. Reads pages[].markdown and
        pages[].images[].image_base64; empty `pages` is treated as failure;
        pages are joined with "# PAGE n" headers when there is more than one.
Step 4 deleteMistralFile: DELETE /files/{id}, always, also after an error.
Every call carries `Authorization: Bearer <key>` (checked by Caddy, not here)."""

from fixtures import make_image, make_pdf

HEADERS = {"Authorization": "Bearer librechat-test-key"}
MODEL = "lightonai/LightOnOCR-3-4B"


def process_ocr_result(result: dict) -> str:
    """LibreChat's processOCRResult, ported."""
    pages = result["pages"]
    text = ""
    for index, page in enumerate(pages):
        if len(pages) > 1:
            text += f"# PAGE {index + 1}\n"
        text += page["markdown"] + "\n\n"
        for image in page.get("images") or []:
            assert "image_base64" in image
    return text


async def librechat_sequence(client, data: bytes, filename: str, document_type: str):
    r = await client.post(
        "/v1/files",
        headers=HEADERS,
        data={"purpose": "ocr"},
        files={"file": (filename, data)},
    )
    assert r.status_code == 200, r.text
    file_id = r.json()["id"]
    assert isinstance(file_id, str) and file_id
    try:
        r = await client.get(f"/v1/files/{file_id}/url", headers=HEADERS, params={"expiry": 24})
        assert r.status_code == 200, r.text
        signed_url = r.json()["url"]
        assert isinstance(signed_url, str) and signed_url.startswith("http")
        body = {
            "model": MODEL,
            "image_limit": 0,
            "include_image_base64": False,
            "document": {"type": document_type, document_type: signed_url},
        }
        return await client.post("/v1/ocr", headers=HEADERS, json=body)
    finally:
        r = await client.delete(f"/v1/files/{file_id}", headers=HEADERS)
        assert r.status_code == 200, r.text


async def test_pdf_round_trip(client):
    r = await librechat_sequence(
        client, make_pdf(["text", "scan"]), "Amtliches Mitteilungsblatt 01-2025.pdf", "document_url"
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert isinstance(result["pages"], list) and len(result["pages"]) == 2
    for page in result["pages"]:
        assert isinstance(page["markdown"], str)
    text = process_ocr_result(result)
    assert text.startswith("# PAGE 1\n")
    assert "# PAGE 2\n" in text
    assert "Absatz 0:" in text


async def test_single_page_has_no_page_header(client):
    r = await librechat_sequence(client, make_pdf(["text"]), "one.pdf", "document_url")
    assert "# PAGE" not in process_ocr_result(r.json())


async def test_image_round_trip_uses_image_url(client):
    r = await librechat_sequence(client, make_image("PNG"), "scan.png", "image_url")
    assert r.status_code == 200 and len(r.json()["pages"]) == 1


async def test_failure_path_still_allows_delete(client):
    # LibreChat deletes the file after an error too; the delete must succeed.
    r = await librechat_sequence(
        client, make_pdf(["text"], encrypt="pw"), "locked.pdf", "document_url"
    )
    assert r.status_code == 422
