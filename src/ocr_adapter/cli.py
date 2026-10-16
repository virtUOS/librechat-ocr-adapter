"""Run a file through the OCR service the way LibreChat does.

    librechat-ocr document.pdf --base-url https://ocr.example.org --key "$OCR_API_KEY" \
        [--pages 115-121] [--json]

Upload, fetch the file URL, call /ocr, delete the upload (also on error), print the
Markdown joined like LibreChat ("# PAGE n" headers) or the raw JSON with --json.
"""

import argparse
import io
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
DEFAULT_MODEL = "lightonai/LightOnOCR-3-4B"


def parse_pages(spec: str) -> list[int]:
    """'115-121' -> [114, ..., 120]; '3' -> [2]; '1,3-4' -> [0, 2, 3]."""
    pages: list[int] = []
    for part in spec.split(","):
        first, _, last = part.strip().partition("-")
        start = int(first)
        end = int(last) if last else start
        if start < 1 or end < start:
            raise ValueError(f"bad page range {part!r}")
        pages.extend(range(start - 1, end))
    return pages


def extract_pages(data: bytes, pages: list[int]) -> bytes:
    import pypdfium2 as pdfium

    source = pdfium.PdfDocument(data)
    target = pdfium.PdfDocument.new()
    target.import_pages(source, pages=pages)
    buf = io.BytesIO()
    target.save(buf)
    return buf.getvalue()


def document_type(filename: str) -> str:
    return "image_url" if Path(filename).suffix.lower() in IMAGE_EXTENSIONS else "document_url"


def run_ocr(client: httpx.Client, data: bytes, filename: str, model: str = DEFAULT_MODEL) -> dict:
    r = client.post("/files", data={"purpose": "ocr"}, files={"file": (filename, data)})
    r.raise_for_status()
    file_id = r.json()["id"]
    try:
        r = client.get(f"/files/{file_id}/url", params={"expiry": 24})
        r.raise_for_status()
        url = r.json()["url"]
        key = document_type(filename)
        r = client.post(
            "/ocr",
            json={
                "model": model,
                "image_limit": 0,
                "include_image_base64": False,
                "document": {"type": key, key: url},
            },
        )
        r.raise_for_status()
        return r.json()
    finally:
        client.delete(f"/files/{file_id}")


def join_pages(result: dict) -> str:
    pages = result["pages"]
    if len(pages) == 1:
        return pages[0]["markdown"] + "\n\n"
    return "".join(f"# PAGE {i + 1}\n{p['markdown']}\n\n" for i, p in enumerate(pages))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("file")
    parser.add_argument("--base-url", default=os.environ.get("OCR_BASE_URL"))
    parser.add_argument("--key", default=os.environ.get("OCR_API_KEY"))
    parser.add_argument("--pages", help="1-based page selection for PDFs, e.g. 115-121")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--json", action="store_true", help="print the raw JSON response")
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args(argv)
    if not args.base_url or not args.key:
        parser.error("--base-url and --key are required (or OCR_BASE_URL and OCR_API_KEY)")

    path = Path(args.file)
    data = path.read_bytes()
    if args.pages:
        data = extract_pages(data, parse_pages(args.pages))
    base = args.base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"

    started = time.monotonic()
    with httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {args.key}"},
        timeout=httpx.Timeout(args.timeout, connect=10),
    ) as client:
        try:
            result = run_ocr(client, data, path.name, args.model)
        except httpx.HTTPStatusError as exc:
            print(f"HTTP {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
            return 1
    elapsed = time.monotonic() - started
    sources = Counter(p.get("source", "?") for p in result["pages"])
    print(f"{len(result['pages'])} pages in {elapsed:.1f}s {dict(sources)}", file=sys.stderr)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(join_pages(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
