import io

import pypdfium2 as pdfium
from PIL import Image

from fixtures import make_image, make_pdf


def test_pdf_page_count_and_text_presence():
    pdf = pdfium.PdfDocument(make_pdf(["text", "scan", "blank"]))
    assert len(pdf) == 3
    assert len(pdf[0].get_textpage().get_text_bounded()) > 2000
    assert "Mitteilungsblatt" in pdf[1].get_textpage().get_text_bounded()
    assert pdf[2].get_textpage().get_text_bounded().strip() == ""


def test_encrypted_pdf_cannot_be_opened_without_password():
    try:
        pdfium.PdfDocument(make_pdf(["text"], encrypt="secret"))
    except pdfium.PdfiumError:
        return
    raise AssertionError("expected PdfiumError")


def test_multi_frame_tiff():
    img = Image.open(io.BytesIO(make_image("TIFF", frames=3)))
    assert img.n_frames == 3


def test_exif_orientation_is_written():
    img = Image.open(io.BytesIO(make_image("JPEG", exif_orientation=6)))
    assert img.getexif()[0x0112] == 6
