import pytest

from ocr_adapter.documents import Dimensions, PdfDocument, normalize_text, open_document
from ocr_adapter.errors import Unprocessable

from fixtures import HEADER, INVISIBLE_HEADER, make_pdf

MIN_CHARS = 200
COVERAGE = 0.5


def page(kind: str, **kwargs):
    return PdfDocument(make_pdf([kind], **kwargs)).pages[0]


def test_text_page_has_many_visible_chars_and_no_image():
    p = page("text")
    assert p.visible_chars() > 2000
    assert p.image_coverage() == 0.0
    assert p.needs_ocr(MIN_CHARS, COVERAGE) is False


def test_sparse_page_needs_ocr():
    p = page("sparse")
    assert p.visible_chars() < MIN_CHARS
    assert p.needs_ocr(MIN_CHARS, COVERAGE) is True


def test_scan_page_header_only_and_high_coverage():
    p = page("scan")
    assert p.visible_chars() == len(HEADER)
    assert 0.75 < p.image_coverage() < 0.85
    assert p.needs_ocr(MIN_CHARS, COVERAGE) is True


def test_invisible_layer_is_not_counted():
    p = page("invisible")
    assert p.visible_chars() == len(INVISIBLE_HEADER)
    assert p.needs_ocr(MIN_CHARS, COVERAGE) is True


def test_mixed_page_with_small_figure_uses_text_layer():
    p = page("mixed")
    assert p.visible_chars() > 2000
    assert p.image_coverage() < 0.1
    assert p.needs_ocr(MIN_CHARS, COVERAGE) is False


def test_offpage_image_coverage_is_clipped_to_one():
    p = page("offpage")
    assert p.image_coverage() == 1.0
    assert p.needs_ocr(MIN_CHARS, COVERAGE) is True


def test_blank_page_needs_ocr():
    p = page("blank")
    assert p.visible_chars() == 0
    assert p.needs_ocr(MIN_CHARS, COVERAGE) is True


def test_text_extraction_is_normalised():
    text = page("text").text()
    assert "\r" not in text
    assert "Absatz 0:" in text and "Absatz 29:" in text
    assert "Prüfungsordnung" in text
    assert "\n\n\n" not in text
    assert not text.endswith("\n")


def test_normalize_text_rules():
    assert normalize_text("a  \r\nb\n\n\n\nc\n") == "a\nb\n\nc"
    assert normalize_text("Prüfung") == "Prüfung"  # NFC


def test_render_longest_edge_and_mode():
    img = page("scan").render(2048)
    assert img.mode == "RGB"
    assert 2047 <= max(img.size) <= 2049
    assert img.size[1] > img.size[0]  # portrait A4


def test_dimensions_match_render():
    p = page("text")
    img = p.render(1024)
    d = p.dimensions(1024)
    assert d == Dimensions(dpi=round(72 * 1024 / 842), width=img.size[0], height=img.size[1])


def test_rotated_page_renders_landscape():
    p = page("text", rotate=90)
    img = p.render(2048)
    assert img.size[0] > img.size[1]
    assert 2047 <= max(img.size) <= 2049
    d = p.dimensions(2048)
    assert (d.width, d.height) == img.size
    assert "Absatz 0:" in p.text()


def test_document_pages_are_indexed():
    doc = PdfDocument(make_pdf(["text", "scan", "text"]))
    assert [p.index for p in doc.pages] == [0, 1, 2]
    assert doc.kind == "pdf"
    doc.close()


def test_encrypted_pdf_is_unprocessable():
    with pytest.raises(Unprocessable):
        PdfDocument(make_pdf(["text"], encrypt="secret"))


def test_garbage_is_unprocessable():
    with pytest.raises(Unprocessable):
        PdfDocument(b"%PDF-1.7 but nothing else")


def test_open_document_dispatches_pdf():
    doc = open_document(make_pdf(["text"]), "application/pdf")
    assert isinstance(doc, PdfDocument)
