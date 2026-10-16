import pypdfium2 as pdfium
import pytest

from ocr_adapter import cli as ocr_cli

from fixtures import make_pdf


@pytest.fixture(scope="module")
def cli():
    return ocr_cli


def test_parse_pages(cli):
    assert cli.parse_pages("115-121") == list(range(114, 121))
    assert cli.parse_pages("3") == [2]
    assert cli.parse_pages("1,3-4") == [0, 2, 3]
    with pytest.raises(ValueError):
        cli.parse_pages("0-2")


def test_extract_pages(cli):
    data = make_pdf(["text", "scan", "text"])
    out = cli.extract_pages(data, [1])
    pdf = pdfium.PdfDocument(out)
    assert len(pdf) == 1
    assert "Mitteilungsblatt" in pdf[0].get_textpage().get_text_bounded()


def test_document_type(cli):
    assert cli.document_type("scan.PNG") == "image_url"
    assert cli.document_type("a.tif") == "image_url"
    assert cli.document_type("a.pdf") == "document_url"


def test_join_pages_matches_librechat(cli):
    one = {"pages": [{"markdown": "hello"}]}
    two = {"pages": [{"markdown": "a"}, {"markdown": "b"}]}
    assert cli.join_pages(one) == "hello\n\n"
    assert cli.join_pages(two) == "# PAGE 1\na\n\n# PAGE 2\nb\n\n"


def test_main_requires_base_url_and_key(cli, monkeypatch, tmp_path):
    monkeypatch.delenv("OCR_BASE_URL", raising=False)
    monkeypatch.delenv("OCR_API_KEY", raising=False)
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(SystemExit) as exc:
        cli.main([str(pdf)])
    assert exc.value.code == 2
