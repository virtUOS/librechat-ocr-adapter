"""Open an upload as pages. Only the content decides the type, never the
declared mime type or filename."""

import io

from PIL import Image, UnidentifiedImageError

from ocr_adapter.errors import UnsupportedMediaType

PDF_MAGIC = b"%PDF"
IMAGE_FORMATS = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "BMP": "image/bmp",
    "WEBP": "image/webp",
    "TIFF": "image/tiff",
}


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
