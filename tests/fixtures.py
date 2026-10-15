"""Deterministic test documents, generated at test time. No binaries in git.

PDF page kinds:
  text       30 lines of visible text, no image              -> text layer page
  sparse     2 short visible lines                            -> too little text, OCR
  scan       full-page raster + an 8-word visible header      -> OCR (Mitteilungsblatt case)
  invisible  full-page raster + invisible OCR layer (mode 3)
             + a short visible header                         -> OCR (Bundestag case)
  mixed      30 lines of text + a small figure                -> text layer page
  offpage    a raster drawn far beyond the page box, no text  -> OCR, coverage clipped to 1.0
  blank      nothing                                          -> OCR (no text)
"""

import io

from PIL import Image, ImageDraw
from reportlab.lib import pdfencrypt
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

A4 = (595.0, 842.0)
LOREM = (
    "Die Allgemeine Prüfungsordnung regelt Prüfungen, Fristen und Abschlüsse "
    "für alle Studiengänge der Universität. "
)
HEADER = "Amtliches Mitteilungsblatt Nr. 01/2025 Seite 118"  # 8 words
INVISIBLE_HEADER = "Drucksache 1/1 Deutscher Bundestag 1949"


def scan_image(seed: int = 0) -> Image.Image:
    """A plausible 'scanned page': light background with dark text-like bars."""
    img = Image.new("RGB", (1240, 1754), (245, 245, 240))
    draw = ImageDraw.Draw(img)
    for i, y in enumerate(range(120, 1650, 42)):
        width = 600 + ((i * 97 + seed * 31) % 400)
        draw.rectangle([120, y, 120 + width, y + 16], fill=(25, 25, 25))
    return img


def _lines(n: int, prefix: str = "") -> list[str]:
    return [f"{prefix}Absatz {i}: {LOREM}" for i in range(n)]


def _draw_text(
    c: canvas.Canvas, lines: list[str], *, y: float = 800, size: int = 11, mode: int = 0
) -> None:
    text = c.beginText(72, y)
    text.setFont("Helvetica", size)
    text.setTextRenderMode(mode)
    for line in lines:
        text.textLine(line)
    c.drawText(text)


def _draw_image(
    c: canvas.Canvas,
    img: Image.Image,
    box: tuple[float, float, float, float] = (36, 36, 523, 770),
) -> None:
    x, y, w, h = box
    c.drawImage(ImageReader(img), x, y, width=w, height=h)


def make_pdf(kinds: list[str], *, encrypt: str | None = None, rotate: int = 0) -> bytes:
    buf = io.BytesIO()
    enc = pdfencrypt.StandardEncryption(encrypt) if encrypt else None
    # reportlab's setPageRotation swaps the MediaBox so that `pagesize` is the
    # *displayed* size, while content is drawn in the unrotated box. A landscape
    # pagesize plus /Rotate 90 therefore yields a portrait MediaBox that pdfium
    # displays and renders landscape, with the text still inside the page.
    pagesize = (A4[1], A4[0]) if rotate in (90, 270) else A4
    c = canvas.Canvas(buf, pagesize=pagesize, encrypt=enc)
    for i, kind in enumerate(kinds):
        if rotate:
            c.setPageRotation(rotate)
        if kind == "text":
            _draw_text(c, _lines(30, f"S{i} "))
        elif kind == "sparse":
            _draw_text(c, ["Anlage 3", "Diese Seite bleibt absichtlich leer."])
        elif kind == "scan":
            _draw_image(c, scan_image(i))
            _draw_text(c, [HEADER], y=820, size=8)
        elif kind == "invisible":
            _draw_image(c, scan_image(i))
            # Text render mode lives in the graphics state and persists across
            # text objects, so the invisible layer is wrapped in q/Q.
            c.saveState()
            _draw_text(c, _lines(30, "hidden "), mode=3)
            c.restoreState()
            _draw_text(c, [INVISIBLE_HEADER], y=820, size=8)
        elif kind == "mixed":
            _draw_text(c, _lines(30, f"S{i} "))
            _draw_image(c, scan_image(i), box=(72, 60, 200, 120))
        elif kind == "offpage":
            _draw_image(c, scan_image(i), box=(-300, -300, 1200, 1500))
        elif kind == "blank":
            pass
        else:
            raise ValueError(f"unknown page kind {kind!r}")
        c.showPage()
    c.save()
    return buf.getvalue()


def make_image(
    fmt: str,
    *,
    size: tuple[int, int] = (800, 600),
    mode: str = "RGB",
    exif_orientation: int | None = None,
    frames: int = 1,
) -> bytes:
    buf = io.BytesIO()
    if frames > 1:
        if fmt != "TIFF":
            raise ValueError("multi-frame fixtures are TIFF only")
        imgs = [Image.new("RGB", size, (40 * i, 100, 100)) for i in range(frames)]
        imgs[0].save(buf, format="TIFF", save_all=True, append_images=imgs[1:])
        return buf.getvalue()
    fill = (0, 0, 0, 0) if mode == "RGBA" else (200, 120, 80) if mode == "RGB" else 0
    img = Image.new(mode, size, fill)
    kwargs = {}
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kwargs["exif"] = exif.tobytes()
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()
