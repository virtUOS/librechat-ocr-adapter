import pytest

from ocr_adapter.documents import sniff_content_type
from ocr_adapter.errors import UnsupportedMediaType

from fixtures import make_image, make_pdf


@pytest.mark.parametrize(
    "data, expected",
    [
        (make_pdf(["text"]), "application/pdf"),
        (make_image("PNG"), "image/png"),
        (make_image("JPEG"), "image/jpeg"),
        (make_image("GIF", mode="P"), "image/gif"),
        (make_image("BMP"), "image/bmp"),
        (make_image("WEBP"), "image/webp"),
        (make_image("TIFF"), "image/tiff"),
    ],
)
def test_sniff_supported(data, expected):
    assert sniff_content_type(data) == expected


@pytest.mark.parametrize("data", [b"", b"hello world", b"PK\x03\x04 not a pdf", b"%PDX"])
def test_sniff_rejects(data):
    with pytest.raises(UnsupportedMediaType):
        sniff_content_type(data)
