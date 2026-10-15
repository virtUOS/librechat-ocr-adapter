"""Open an upload as pages. Only the content decides the type, never the
declared mime type or filename."""

import io
import math
import re
import unicodedata
from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as raw
from PIL import Image, ImageOps, UnidentifiedImageError

from ocr_adapter.errors import TooLarge, Unprocessable, UnsupportedMediaType

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
    except Image.DecompressionBombError as exc:
        raise TooLarge("image exceeds the pixel limit") from exc
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
        # Object bounds are in unrotated user space with the page box's own
        # origin, so clip against the box itself, not the displayed size.
        box_left, box_bottom, box_right, box_top = self._page.get_bbox()
        area = (box_right - box_left) * (box_top - box_bottom)
        if area <= 0:
            return 0.0
        covered = 0.0
        for obj in self._page.get_objects(max_depth=_OBJECT_DEPTH):
            if obj.type != raw.FPDF_PAGEOBJ_IMAGE:
                continue
            left, bottom, right, top = obj.get_bounds()
            w = max(0.0, min(right, box_right) - max(left, box_left))
            h = max(0.0, min(top, box_top) - max(bottom, box_bottom))
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

    def __init__(self, data: bytes, max_pages: int | None = None):
        try:
            self._pdf = pdfium.PdfDocument(data)
            count = len(self._pdf)
            if count == 0:
                raise Unprocessable("PDF has no pages")
            if max_pages is not None and count > max_pages:
                raise TooLarge(f"{count} pages exceed the limit of {max_pages}")
            self._pdf.init_forms()  # must precede page loading for form fields to render
            self.pages = [PdfPage(self._pdf[i], i) for i in range(count)]
        except pdfium.PdfiumError as exc:
            raise Unprocessable(f"cannot open PDF: {exc}") from exc

    def close(self) -> None:
        self._pdf.close()


def _flatten(img: Image.Image) -> Image.Image:
    """Apply EXIF orientation, put transparency on white, return RGB."""
    img = ImageOps.exif_transpose(img)
    has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
    if has_alpha:
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return img.convert("RGB")


def _fit(width: int, height: int, longest_edge: int) -> tuple[int, int]:
    """Size after shrinking so the longest edge is at most longest_edge; never upscales."""
    longest = max(width, height)
    if longest <= longest_edge:
        return width, height
    scale = longest_edge / longest
    return max(1, round(width * scale)), max(1, round(height * scale))


def _oriented_size(img: Image.Image) -> tuple[int, int]:
    """The size after EXIF orientation, read from the header without decoding."""
    width, height = img.size
    try:
        orientation = img.getexif().get(0x0112, 1)
    except Exception:  # noqa: BLE001 - a broken EXIF block must not fail the upload
        orientation = 1
    return (height, width) if orientation in (5, 6, 7, 8) else (width, height)


class ImagePage:
    """One image or TIFF frame. Pixels are decoded only in render(), one page at
    a time, so a document never holds more than one full-resolution frame."""

    def __init__(self, data: bytes, frame: int, size: tuple[int, int], dpi: float, index: int):
        self._data = data
        self._frame = frame
        self._size = size  # oriented, full resolution
        self._dpi = dpi
        self.index = index

    def visible_chars(self) -> int:
        return 0

    def image_coverage(self) -> float:
        return 1.0

    def needs_ocr(self, min_visible_chars: int, image_coverage: float) -> bool:
        return True

    def text(self) -> str:
        return ""

    def render(self, longest_edge: int) -> Image.Image:
        try:
            with Image.open(io.BytesIO(self._data)) as img:
                img.seek(self._frame)
                img.load()
                flat = _flatten(img)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise Unprocessable(f"cannot decode image: {exc}") from exc
        target = _fit(*flat.size, longest_edge)
        if target != flat.size:
            flat = flat.resize(target, Image.Resampling.LANCZOS)
        return flat

    def dimensions(self, longest_edge: int) -> Dimensions:
        width, height = _fit(*self._size, longest_edge)
        return Dimensions(dpi=round(self._dpi), width=width, height=height)


class ImageDocument:
    kind = "image"

    def __init__(self, data: bytes, max_pixels: int | None = None, max_pages: int | None = None):
        try:
            img = Image.open(io.BytesIO(data))  # header only, no pixel data yet
            # Only TIFF frames are pages; an animated GIF is one picture.
            frame_count = getattr(img, "n_frames", 1) if img.format == "TIFF" else 1
            if max_pages is not None and frame_count > max_pages:
                raise TooLarge(f"{frame_count} pages exceed the limit of {max_pages}")
            dpi = img.info.get("dpi", (72, 72))[0]
            sizes = []
            for i in range(frame_count):
                img.seek(i)
                width, height = img.size
                if max_pixels is not None and width * height > max_pixels:
                    raise TooLarge(f"image has {width * height} pixels, limit {max_pixels}")
                sizes.append(_oriented_size(img))
        except Image.DecompressionBombError as exc:
            raise TooLarge("image exceeds the pixel limit") from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise Unprocessable(f"cannot read image: {exc}") from exc
        self.pages = [ImagePage(data, i, size, dpi, i) for i, size in enumerate(sizes)]

    def close(self) -> None:
        return None


Page = PdfPage | ImagePage
Document = PdfDocument | ImageDocument


def open_document(
    data: bytes,
    content_type: str,
    max_pixels: int | None = None,
    max_pages: int | None = None,
) -> Document:
    if content_type == "application/pdf":
        return PdfDocument(data, max_pages=max_pages)
    return ImageDocument(data, max_pixels=max_pixels, max_pages=max_pages)
