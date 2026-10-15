# ocr-adapter

Implements the four Mistral OCR API calls LibreChat v0.8.8 makes (`POST /v1/files`,
`GET /v1/files/{id}/url`, `POST /v1/ocr`, `DELETE /v1/files/{id}`) on top of a vLLM
serving `lightonai/LightOnOCR-3-4B`. PDF pages with a usable visible text layer are
extracted directly; scanned pages and images are rendered and sent to the model.

## Develop

```
uv sync
uv run pytest
uv run ruff check .
```

## Dependencies in the image

`requirements.txt` is exported from `uv.lock` and is what the Dockerfile installs, so the
image is reproducible. After changing `pyproject.toml` or running `uv lock`, regenerate it:

```
uv export --frozen --no-dev --no-hashes --no-emit-project -o requirements.txt
```

## Configuration

All settings are environment variables with the prefix `OCR_`; see `src/ocr_adapter/config.py`.
`OCR_PUBLIC_URL` is required (the public base URL that file URLs are built from).

## Run

```
docker build -t ocr-adapter:local .
docker run -d --name ocr-adapter --network <network with vllm> \
  --mount type=tmpfs,destination=/uploads,tmpfs-size=2147483648,tmpfs-mode=1777 \
  -e OCR_VLLM_URL=http://vllm:8000 -e OCR_PUBLIC_URL=https://<public host> \
  ocr-adapter:local
```

Uploads live only on the tmpfs and are removed by `DELETE`, by the TTL sweep
(`OCR_FILE_TTL_S`, default one hour) and by any restart. OCR results are never stored,
and logs carry ids, counts and timings only.

## Measurements (ocr-test, RTX 3090, vLLM v0.22.0, 2026-10-15)

| Check | Result |
|---|---|
| Direct 2048 px render vs 400 DPI then LANCZOS downscale, Mitteilungsblatt 01/2025 p. 120 | word-level similarity 0.994; the only difference was a caption rendered in italics with a rule vs plain; direct render kept |
| 20 scanned pages (Mitteilungsblatt 118–137), `OCR_CONCURRENCY=1` | 172.9 s |
| Same, `OCR_CONCURRENCY=4` (default) | 60.3 s |
| One dense table page (p. 120) alone, prompt / completion tokens | 2,894 / 1,193, about 16 s |

## API

| Route | Behaviour |
|---|---|
| `POST /v1/files` (multipart `purpose=ocr`, `file`) | stores the upload on the tmpfs; returns a Mistral file object with `id` |
| `GET /v1/files/{id}/url` | `{"url": "<OCR_PUBLIC_URL>/v1/files/{id}/content"}`; `expiry` is ignored, the TTL governs |
| `GET /v1/files/{id}/content` | the stored bytes |
| `DELETE /v1/files/{id}` | removes the upload |
| `POST /v1/ocr` | `{"model", "document": {"type": "document_url"\|"image_url", "<type>": url}}`; `url` is one of this service's file URLs or a base64 `data:` URL; other fields are ignored |
| `GET /health` | `{"status": "ok", "vllm": "ok"\|"unreachable"}` |
| `GET /metrics` | Prometheus text |

The `/v1/ocr` response is Mistral-shaped: `model`, `pages[{index, markdown, images: [], dimensions{dpi, width, height}, source}]`,
`usage_info{pages_processed, doc_size_bytes}`. `source` is an extension: `text` when the page's visible text layer was
used, `ocr` when the page was rendered and transcribed by the model.

Errors are JSON `{"message", "type"}`: 400 `invalid_request`, 404 `not_found`, 413 `too_large`, 415 `unsupported_media_type`,
422 `unprocessable`, 499 `client_disconnected`, 502 `upstream_error`, 503 `upstream_unavailable` / `storage_full`, 504 `timeout`.
