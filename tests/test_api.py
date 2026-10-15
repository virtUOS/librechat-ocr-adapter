import base64

import httpx

from ocr_adapter.app import create_app

from fixtures import make_image, make_pdf

MODEL = "lightonai/LightOnOCR-3-4B"


async def upload(client, data: bytes, filename="doc.pdf", purpose="ocr") -> httpx.Response:
    return await client.post(
        "/v1/files", data={"purpose": purpose}, files={"file": (filename, data)}
    )


async def url_of(client, file_id: str) -> str:
    r = await client.get(f"/v1/files/{file_id}/url", params={"expiry": 24})
    assert r.status_code == 200, r.text
    return r.json()["url"]


def ocr_body(url: str, type_="document_url") -> dict:
    return {
        "model": MODEL,
        "image_limit": 0,
        "include_image_base64": False,
        "document": {"type": type_, type_: url},
    }


async def uploaded_url(client, data: bytes, filename="doc.pdf") -> str:
    r = await upload(client, data, filename)
    assert r.status_code == 200, r.text
    return await url_of(client, r.json()["id"])


async def test_upload_returns_mistral_file_object(client):
    r = await upload(client, make_pdf(["text"]), "Prüfungsordnung.pdf")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "file" and body["purpose"] == "ocr"
    assert body["filename"] == "Prüfungsordnung.pdf"
    assert body["bytes"] > 0 and isinstance(body["created_at"], int)
    assert len(body["id"]) == 32


async def test_upload_without_filename_still_works(client):
    r = await client.post("/v1/files", data={"purpose": "ocr"}, files={"file": make_pdf(["text"])})
    assert r.status_code == 200


async def test_upload_wrong_purpose(client):
    r = await upload(client, make_pdf(["text"]), purpose="fine-tune")
    assert r.status_code == 400 and r.json()["type"] == "invalid_request"


async def test_upload_missing_purpose_is_400(client):
    r = await client.post("/v1/files", files={"file": ("x.pdf", make_pdf(["text"]))})
    assert r.status_code == 400 and r.json()["type"] == "invalid_request"


async def test_upload_too_large(client):
    r = await upload(client, b"%PDF-1.7\n" + b"x" * (1024 * 1024))
    assert r.status_code == 413 and r.json()["type"] == "too_large"


async def test_upload_garbage_and_empty_are_415(client):
    for data in (b"", b"hello"):
        r = await upload(client, data, "notes.txt")
        assert r.status_code == 415 and r.json()["type"] == "unsupported_media_type"


async def test_file_url_content_and_delete(client, settings):
    data = make_pdf(["text"])
    file_id = (await upload(client, data)).json()["id"]
    url = await url_of(client, file_id)
    assert url == f"{settings.public_url}/v1/files/{file_id}/content"
    r = await client.get(f"/v1/files/{file_id}/content")
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-type"] == "application/pdf"
    r = await client.delete(f"/v1/files/{file_id}")
    assert r.json() == {"id": file_id, "object": "file", "deleted": True}
    assert (await client.delete(f"/v1/files/{file_id}")).status_code == 404
    assert (await client.get(f"/v1/files/{file_id}/url")).status_code == 404
    assert (await client.get(f"/v1/files/{file_id}/content")).status_code == 404
    assert list(settings.upload_dir.iterdir()) == []


async def test_ocr_mixed_pdf(client, fake, settings):
    url = await uploaded_url(client, make_pdf(["text", "scan"]))
    r = await client.post("/v1/ocr", json=ocr_body(url))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == settings.model
    assert [p["index"] for p in body["pages"]] == [0, 1]
    assert [p["source"] for p in body["pages"]] == ["text", "ocr"]
    assert "Absatz 0:" in body["pages"][0]["markdown"]
    assert body["pages"][1]["markdown"].startswith("ocr:")
    assert all(p["images"] == [] for p in body["pages"])
    assert set(body["pages"][1]["dimensions"]) == {"dpi", "width", "height"}
    assert body["usage_info"]["pages_processed"] == 2
    assert body["usage_info"]["doc_size_bytes"] > 0
    assert len(fake.requests) == 1


async def test_ocr_image_with_image_url_type(client):
    url = await uploaded_url(client, make_image("PNG"), "scan.png")
    r = await client.post("/v1/ocr", json=ocr_body(url, "image_url"))
    assert r.status_code == 200
    assert [p["source"] for p in r.json()["pages"]] == ["ocr"]


async def test_ocr_sniffs_content_not_declared_type(client):
    url = await uploaded_url(client, make_pdf(["text"]), "actually-a.png")
    r = await client.post("/v1/ocr", json=ocr_body(url, "image_url"))
    assert r.status_code == 200 and r.json()["pages"][0]["source"] == "text"


async def test_ocr_data_url(client):
    payload = base64.b64encode(make_pdf(["text"])).decode()
    r = await client.post("/v1/ocr", json=ocr_body(f"data:application/pdf;base64,{payload}"))
    assert r.status_code == 200 and r.json()["pages"][0]["source"] == "text"


async def test_ocr_data_url_bad_base64(client):
    r = await client.post("/v1/ocr", json=ocr_body("data:application/pdf;base64,@@@not-base64@@@"))
    assert r.status_code == 400 and r.json()["type"] == "invalid_request"


async def test_ocr_data_url_not_base64_encoded(client):
    r = await client.post("/v1/ocr", json=ocr_body("data:text/plain,hello"))
    assert r.status_code == 400


async def test_ocr_data_url_too_large(client):
    payload = "A" * (1024 * 1024 * 4 // 3 + 100)
    r = await client.post("/v1/ocr", json=ocr_body(f"data:application/pdf;base64,{payload}"))
    assert r.status_code == 413


async def test_ocr_foreign_url_is_rejected(client):
    r = await client.post("/v1/ocr", json=ocr_body("https://example.org/secret.pdf"))
    assert r.status_code == 400 and r.json()["type"] == "invalid_request"


async def test_ocr_unknown_file_id(client, settings):
    r = await client.post("/v1/ocr", json=ocr_body(f"{settings.public_url}/v1/files/nope/content"))
    assert r.status_code == 404 and r.json()["type"] == "not_found"


async def test_ocr_missing_document_url_field(client):
    r = await client.post("/v1/ocr", json={"model": MODEL, "document": {"type": "document_url"}})
    assert r.status_code == 400


async def test_ocr_encrypted_pdf_is_422(client):
    url = await uploaded_url(client, make_pdf(["text"], encrypt="pw"))
    r = await client.post("/v1/ocr", json=ocr_body(url))
    assert r.status_code == 422 and r.json()["type"] == "unprocessable"


async def test_ocr_too_many_pages_is_413(client):
    url = await uploaded_url(client, make_pdf(["text"] * 6))  # settings.max_pages == 5
    r = await client.post("/v1/ocr", json=ocr_body(url))
    assert r.status_code == 413 and r.json()["type"] == "too_large"


async def test_ocr_vllm_failure_is_502(client, fake):
    fake.fail_times = 999
    url = await uploaded_url(client, make_pdf(["scan"]))
    r = await client.post("/v1/ocr", json=ocr_body(url))
    assert r.status_code == 502 and r.json()["type"] == "upstream_error"


async def test_ocr_vllm_unreachable_is_503(settings):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://vllm")
    app = create_app(settings, http=http)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter"
    ) as client:
        url = await uploaded_url(client, make_pdf(["scan"]))
        r = await client.post("/v1/ocr", json=ocr_body(url))
        assert r.status_code == 503 and r.json()["type"] == "upstream_unavailable"
        assert (await client.get("/health")).json() == {"status": "ok", "vllm": "unreachable"}


async def test_ocr_deadline_is_504(settings, fake, vllm_http):
    fake.delay = 1.0
    app = create_app(settings.model_copy(update={"document_timeout_s": 0.2}), http=vllm_http)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter"
    ) as client:
        url = await uploaded_url(client, make_pdf(["scan"]))
        r = await client.post("/v1/ocr", json=ocr_body(url))
        assert r.status_code == 504 and r.json()["type"] == "timeout"


async def test_malformed_json_is_400(client):
    r = await client.post(
        "/v1/ocr", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert r.status_code == 400 and r.json()["type"] == "invalid_request"


async def test_health_and_metrics(client):
    assert (await client.get("/health")).json() == {"status": "ok", "vllm": "ok"}
    r = await client.get("/metrics")
    assert r.status_code == 200 and "ocr_pages_total" in r.text


async def test_unknown_route_is_json_404(client):
    r = await client.get("/v1/models")
    assert r.status_code == 404 and r.json()["type"] == "not_found"


async def test_logs_never_contain_filenames(client, caplog):
    caplog.set_level("INFO")
    await uploaded_url(client, make_pdf(["text"]), "Geheimes Gutachten Müller.pdf")
    assert "Gutachten" not in caplog.text and "Müller" not in caplog.text
