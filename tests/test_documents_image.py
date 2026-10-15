import io
from unittest.mock import patch

import pytest
from PIL import Image, ImageFile

from ocr_adapter.documents import ImageDocument, ImagePage, open_document, sniff_content_type
from ocr_adapter.errors import TooLarge, Unprocessable

from fixtures import make_image


def test_png_is_one_ocr_page():
    doc = ImageDocument(make_image("PNG", size=(800, 600)))
    assert doc.kind == "image"
    assert len(doc.pages) == 1
    p = doc.pages[0]
    assert isinstance(p, ImagePage)
    assert p.index == 0
    assert p.needs_ocr(200, 0.5) is True
    assert p.text() == ""


def test_small_image_is_not_upscaled():
    p = ImageDocument(make_image("PNG", size=(800, 600))).pages[0]
    img = p.render(2048)
    assert img.size == (800, 600)
    assert img.mode == "RGB"
    assert p.dimensions(2048).width == 800 and p.dimensions(2048).height == 600


def test_large_image_is_downscaled_to_longest_edge():
    p = ImageDocument(make_image("JPEG", size=(4000, 3000))).pages[0]
    img = p.render(2048)
    assert max(img.size) == 2048
    assert img.size[0] > img.size[1]
    assert (p.dimensions(2048).width, p.dimensions(2048).height) == img.size


def test_exif_rotation_is_applied():
    p = ImageDocument(make_image("JPEG", size=(800, 600), exif_orientation=6)).pages[0]
    assert p.render(2048).size == (600, 800)


def test_transparent_png_is_flattened_on_white():
    p = ImageDocument(make_image("PNG", size=(10, 10), mode="RGBA")).pages[0]
    assert p.render(2048).getpixel((0, 0)) == (255, 255, 255)


def test_palette_png_with_transparency_is_flattened():
    img = Image.new("P", (10, 10), 0)
    img.info["transparency"] = 0
    buf = io.BytesIO()
    img.save(buf, format="PNG", transparency=0)
    p = ImageDocument(buf.getvalue()).pages[0]
    assert p.render(2048).getpixel((0, 0)) == (255, 255, 255)


def test_cmyk_jpeg_renders_rgb():
    p = ImageDocument(make_image("JPEG", size=(20, 20), mode="CMYK")).pages[0]
    img = p.render(2048)
    assert img.mode == "RGB"
    assert img.getpixel((5, 5)) == (255, 255, 255)  # CMYK 0,0,0,0 is white


def test_colour_key_transparency_is_flattened_on_white():
    img = Image.new("RGB", (10, 10), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG", transparency=(0, 0, 0))  # black is the transparent key
    p = ImageDocument(buf.getvalue()).pages[0]
    assert p.render(2048).getpixel((0, 0)) == (255, 255, 255)


def test_multi_frame_tiff_is_one_page_per_frame():
    doc = ImageDocument(make_image("TIFF", size=(300, 200), frames=3))
    assert [p.index for p in doc.pages] == [0, 1, 2]
    assert doc.pages[2].render(2048).size == (300, 200)


def test_animated_gif_is_one_page():
    frames = [Image.new("P", (20, 20), i) for i in range(3)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
    assert len(ImageDocument(buf.getvalue()).pages) == 1


def test_truncated_image_is_unprocessable_when_rendered():
    data = make_image("JPEG", size=(800, 600))[:200]
    with pytest.raises(Unprocessable):
        ImageDocument(data).pages[0].render(2048)


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_oversized_image_is_rejected_before_decoding():
    data = make_image("PNG", size=(12000, 12000), mode="1")  # 144 MP in a few KB
    assert len(data) < 200_000
    with pytest.raises(TooLarge):
        ImageDocument(data, max_pixels=50_000_000)


def test_decompression_bomb_is_too_large():
    data = make_image("PNG", size=(15000, 15000), mode="1")  # above Pillow's hard limit
    with pytest.raises(TooLarge):
        sniff_content_type(data)
    with pytest.raises(TooLarge):
        ImageDocument(data)


def test_frames_are_decoded_only_when_rendered():
    data = make_image("TIFF", size=(300, 200), frames=3)
    with patch.object(ImageFile.ImageFile, "load", autospec=True) as load:
        doc = ImageDocument(data)
        assert doc.pages[2].dimensions(2048).width == 300
    assert load.call_count == 0
    assert doc.pages[2].render(2048).size == (300, 200)


def test_open_document_dispatches_image():
    assert isinstance(open_document(make_image("PNG"), "image/png"), ImageDocument)
