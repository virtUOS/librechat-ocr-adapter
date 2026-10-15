"""Open an upload as pages. Only the content decides the type, never the
declared mime type or filename."""

import io
import math
import re
import unicodedata
from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as raw
from PIL import Image, UnidentifiedImageError

from ocr_adapter.errors import Unprocessable, UnsupportedMediaType

PDF_MAGIC = b"%PDF"
IMAGE_FORMATS = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "BMP": "image/bmp",
    "WEBP": "image/webp",
    "TIFF": "image/tiff",
}

# Text render modes 3 (invisible) and 7 (clip only) draw nothing. An OCR layer
# that an earlier tool put under a scan uses mode 3; it must not count as text.
INVISIBLE_MODES = {3, 7}
_OBJECT_DEPTH = 8  # recurse into form XObjects


def sniff_content_type(data: bytes) -> str:
    """Return the content type of PDF or supported image bytes, else raise 415."""
    if PDF_MAGIC in data[:1024]:
        return "application/pdf"
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UnsupportedMediaType("not a PDF or a supported image") from exc
    if fmt not in IMAGE_FORMATS:
        raise UnsupportedMediaType(f"unsupported image format {fmt}")
    return IMAGE_FORMATS[fmt]


@dataclass(frozen=True)
class Dimensions:
    dpi: int
    width: int
    height: int


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


def _scaled(width_pt: float, height_pt: float, longest_edge: int) -> tuple[float, int, int]:
    """Scale factor and pixel size so that the longest edge is longest_edge px.
    pypdfium2 sizes the bitmap with math.ceil, so the same rounding is used here."""
    scale = longest_edge / max(width_pt, height_pt)
    return scale, math.ceil(width_pt * scale), math.ceil(height_pt * scale)


class PdfPage:
    def __init__(self, page: pdfium.PdfPage, index: int):
        self._page = page
        self._textpage: pdfium.PdfTextPage | None = None
        self.index = index

    def _text_page(self) -> pdfium.PdfTextPage:
        if self._textpage is None:
            self._textpage = self._page.get_textpage()
        return self._textpage

    def visible_chars(self) -> int:
        textpage = self._text_page()
        total = 0
        for obj in self._page.get_objects(max_depth=_OBJECT_DEPTH):
            if obj.type != raw.FPDF_PAGEOBJ_TEXT:
                continue
            if raw.FPDFTextObj_GetTextRenderMode(obj.raw) in INVISIBLE_MODES:
                continue
            # Returns the UTF-16 byte length including the two-byte terminator.
            nbytes = raw.FPDFTextObj_GetText(obj.raw, textpage.raw, None, 0)
            total += max((nbytes - 2) // 2, 0)
        return total

    def image_coverage(self) -> float:
        width, height = self._page.get_size()
        area = width * height
        if area <= 0:
            return 0.0
        covered = 0.0
        for obj in self._page.get_objects(max_depth=_OBJECT_DEPTH):
            if obj.type != raw.FPDF_PAGEOBJ_IMAGE:
                continue
            left, bottom, right, top = obj.get_bounds()
            w = max(0.0, min(right, width) - max(left, 0.0))
            h = max(0.0, min(top, height) - max(bottom, 0.0))
            covered += w * h
        return min(covered / area, 1.0)

    def needs_ocr(self, min_visible_chars: int, image_coverage: float) -> bool:
        return self.visible_chars() < min_visible_chars or self.image_coverage() > image_coverage

    def text(self) -> str:
        return normalize_text(self._text_page().get_text_bounded())

    def render(self, longest_edge: int) -> Image.Image:
        scale, _, _ = _scaled(*self._page.get_size(), longest_edge)
        bitmap = self._page.render(scale=scale)
        return bitmap.to_pil().convert("RGB")

    def dimensions(self, longest_edge: int) -> Dimensions:
        scale, width, height = _scaled(*self._page.get_size(), longest_edge)
        return Dimensions(dpi=round(72 * scale), width=width, height=height)


class PdfDocument:
    kind = "pdf"

    def __init__(self, data: bytes):
        try:
            self._pdf = pdfium.PdfDocument(data)
            count = len(self._pdf)
        except pdfium.PdfiumError as exc:
            raise Unprocessable(f"cannot open PDF: {exc}") from exc
        if count == 0:
            raise Unprocessable("PDF has no pages")
        self._pdf.init_forms()  # must precede page loading for form fields to render
        self.pages = [PdfPage(self._pdf[i], i) for i in range(count)]

    def close(self) -> None:
        self._pdf.close()


def open_document(data: bytes, content_type: str):
    if content_type == "application/pdf":
        return PdfDocument(data)
    raise UnsupportedMediaType(f"cannot open {content_type}")  # replaced in Task 4
