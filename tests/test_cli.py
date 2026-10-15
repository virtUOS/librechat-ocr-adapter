import importlib.util
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from fixtures import make_pdf

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ocr.py"


@pytest.fixture(scope="module")
def cli():
    spec = importlib.util.spec_from_file_location("ocr_cli", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
