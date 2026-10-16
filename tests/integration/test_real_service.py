"""Against a real adapter + vLLM:

    OCR_TEST_BASE_URL=https://ocr.example.org OCR_TEST_API_KEY=... \
        uv run pytest tests/integration -v
"""

import io

import pypdfium2 as pdfium

UMLAUT_WORDS = ("für", "Prüfung", "Universität", "Änderung", "Osnabrück")


def test_born_digital_uses_text_layer_only(cli, fetch, service):
    data = cli.extract_pages(fetch("po"), list(range(0, 5)))
    result = cli.run_ocr(service, data, "po.pdf")
    assert [p["source"] for p in result["pages"]] == ["text"] * 5
    # The title page is set in capitals: "ALLGEMEINE PRÜFUNGSORDNUNG".
    assert "prüfungsordnung" in result["pages"][0]["markdown"].lower()


def test_mixed_document_pages_115_to_121(cli, fetch, service):
    data = cli.extract_pages(fetch("mitteilungsblatt"), cli.parse_pages("115-121"))
    result = cli.run_ocr(service, data, "mitteilungsblatt-115-121.pdf")
    sources = [p["source"] for p in result["pages"]]
    assert sources == ["text", "text", "text", "ocr", "ocr", "ocr", "ocr"]
    page_120 = result["pages"][5]["markdown"]
    assert "<table" in page_120.lower()
    ocr_text = " ".join(p["markdown"] for p in result["pages"] if p["source"] == "ocr")
    assert any(word in ocr_text for word in UMLAUT_WORDS)


def test_invisible_ocr_layer_is_reocrd(cli, fetch, service):
    data = cli.extract_pages(fetch("bundestag"), [0, 1])
    result = cli.run_ocr(service, data, "bundestag.pdf")
    assert [p["source"] for p in result["pages"]] == ["ocr", "ocr"]
    assert "Bundestag" in result["pages"][0]["markdown"]


def test_image_upload(cli, fetch, service):
    page = pdfium.PdfDocument(fetch("po"))[0]
    image = page.render(scale=2).to_pil().convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    result = cli.run_ocr(service, buf.getvalue(), "page.png")
    assert len(result["pages"]) == 1 and result["pages"][0]["source"] == "ocr"
    assert "prüfungsordnung" in result["pages"][0]["markdown"].lower()
