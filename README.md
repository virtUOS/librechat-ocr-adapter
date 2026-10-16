# librechat-ocr-adapter

Document OCR for [LibreChat](https://www.librechat.ai/) without Mistral's cloud: this service
implements the subset of the Mistral OCR API that LibreChat's `mistral_ocr` strategy calls
(`POST /v1/files`, `GET /v1/files/{id}/url`, `POST /v1/ocr`, `DELETE /v1/files/{id}`) on top of
a [vLLM](https://github.com/vllm-project/vllm) serving
[`lightonai/LightOnOCR-3-4B`](https://huggingface.co/lightonai/LightOnOCR-3-4B). PDF pages with
a usable text layer are extracted directly; scanned pages and images are rendered and sent to
the model. Tested against LibreChat v0.8.8 (a contract test replays its upload sequence).

It runs in production for the LibreChat instances of Osnabrück University.

## Quick start

[`examples/docker-compose.yml`](examples/docker-compose.yml) starts vLLM with the model (one
NVIDIA GPU, about 9 GB of weights downloaded on first start) and the adapter next to it. Set
`OCR_PUBLIC_URL` in it to the URL LibreChat will reach the service under, then:

```
docker compose -f examples/docker-compose.yml up -d
curl -s http://127.0.0.1:8000/health
```

`{"status":"ok","vllm":"unreachable"}` while the model loads, `{"status":"ok","vllm":"ok"}` once
vLLM is up (the first start takes several minutes; the compose file allows ten).

The image is published at `ghcr.io/virtuos/librechat-ocr-adapter` for `linux/amd64` and
`linux/arm64`; pin a version tag, `latest` moves.

## LibreChat

In `librechat.yaml`:

```yaml
ocr:
  strategy: mistral_ocr
  mistralModel: lightonai/LightOnOCR-3-4B
  apiKey: "${OCR_API_KEY}"
  baseURL: https://ocr.example.org/v1

fileConfig:
  ocr:
    supportedMimeTypes:
      - "^application/pdf$"
      - "^image/(jpeg|png|gif|webp|bmp|tiff)$"
```

`apiKey` is whatever your reverse proxy expects (next section); the adapter itself does not
check it. The MIME list sends only PDFs and images to OCR; other formats stay on LibreChat's
built-in parser. If the service fails, LibreChat logs the error and falls back to
`document_parser` for that upload.

## Security

The adapter authenticates nobody, and `GET /v1/files/{id}/content` serves an upload to whoever
knows its id. Never publish port 8000. Run it behind a reverse proxy that checks a bearer key
on `/v1/*` and leaves `/health` open. With [Caddy](https://caddyserver.com/):

```
ocr.example.org {
	@unauthorized not header Authorization "Bearer {$OCR_API_KEY}"

	handle /v1/* {
		respond @unauthorized "Unauthorized" 401
		request_body {
			max_size 100MB
		}
		reverse_proxy ocr-adapter:8000
	}
	handle /health {
		reverse_proxy ocr-adapter:8000
	}
	handle {
		respond "Not found" 404
	}
}
```

Uploads live on a tmpfs only and are removed by `DELETE`, by the TTL sweep (`OCR_FILE_TTL_S`)
and by any restart. OCR results are never stored. Logs carry ids, counts and timings, never
document content.

## Configuration

Every setting is an environment variable with the prefix `OCR_`
(`src/ocr_adapter/config.py`).

| Variable | Default | Meaning |
| --- | --- | --- |
| `OCR_PUBLIC_URL` | required | public base URL the file URLs are built from |
| `OCR_VLLM_URL` | `http://vllm:8000` | the vLLM server |
| `OCR_MODEL` | `lightonai/LightOnOCR-3-4B` | model name sent to vLLM |
| `OCR_CONCURRENCY` | `4` | pages in flight at vLLM per process |
| `OCR_MAX_TOKENS` | `4096` | completion cap per page; longer pages are truncated and counted |
| `OCR_TEMPERATURE` | `0.2` | sampling temperature |
| `OCR_TOP_P` | `0.9` | nucleus sampling |
| `OCR_PAGE_TIMEOUT_S` | `180` | one page at vLLM |
| `OCR_DOCUMENT_TIMEOUT_S` | `1200` | one whole document |
| `OCR_MAX_FILE_MB` | `100` | upload size limit (413 above) |
| `OCR_MAX_PAGES` | `100` | pages per document (413 above) |
| `OCR_FILE_TTL_S` | `3600` | uploads older than this are swept |
| `OCR_UPLOAD_DIR` | `/uploads` | where uploads are spooled (mount a tmpfs) |
| `OCR_MIN_VISIBLE_CHARS` | `200` | fewer visible characters on a page means it is OCRed |
| `OCR_IMAGE_COVERAGE` | `0.5` | a page more than half covered by images is OCRed |
| `OCR_LONGEST_EDGE` | `2048` | render size of a page image in pixels |
| `OCR_MAX_IMAGE_PIXELS` | `50000000` | width × height limit for one image or frame (413 above) |
| `OCR_LOG_LEVEL` | `info` | log level |

## CLI

`librechat-ocr` runs a file through a deployed service exactly as LibreChat does (upload, file
URL, `/ocr`, delete) and prints the Markdown joined like LibreChat (`# PAGE n` headers), or the
raw JSON with `--json`. Page count, time and per-page sources go to stderr.

```
uvx --from git+https://github.com/virtUOS/librechat-ocr-adapter@v0.1.0 librechat-ocr \
    document.pdf --base-url https://ocr.example.org --key "$OCR_API_KEY" [--pages 115-121] [--json]
```

The same from the image:

```
docker run --rm -v "$PWD:/data" ghcr.io/virtuos/librechat-ocr-adapter:0.1.0 \
    librechat-ocr /data/document.pdf --base-url https://ocr.example.org --key "$OCR_API_KEY"
```

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

## Metrics

`GET /metrics` serves Prometheus text:

| Series | Meaning |
| --- | --- |
| `ocr_documents_total{status}` | documents processed, by outcome |
| `ocr_pages_total{source}` | pages processed, `text` or `ocr` |
| `ocr_truncated_pages_total` | OCR pages cut off at `OCR_MAX_TOKENS` |
| `ocr_errors_total{type}` | failed requests, by error type (the `type` of the error JSON) |
| `ocr_page_seconds{source}` | histogram of per-page processing time |
| `ocr_document_seconds` | histogram of per-document processing time |

Put the route behind authentication too (basic auth on the proxy, as in the Caddy example's
style) before pointing a Prometheus at it.

## Development

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

Dependencies are pinned in `uv.lock`, which is also what the image installs from; Dependabot
opens update pull requests monthly, and `uv lock --upgrade` does the same by hand.

The integration tests run against a deployed service and are skipped unless both variables are
set; they download three public German PDFs (two from the University of Osnabrück, one from
the Bundestag) into `tests/integration/.cache/` on first run:

```
OCR_TEST_BASE_URL=https://ocr.example.org OCR_TEST_API_KEY=... uv run pytest tests/integration -v
```

Releasing: bump `version` in `pyproject.toml`, add the section to `CHANGELOG.md`, tag `vX.Y.Z`
and push the tag. The release workflow refuses a tag that does not match the packaged version,
then builds the image for both platforms and pushes it to GHCR.

## Measurements (RTX 3090, vLLM v0.22.0, 2026-10-15)

| Check | Result |
|---|---|
| Direct 2048 px render vs 400 DPI then LANCZOS downscale, Mitteilungsblatt 01/2025 p. 120 | word-level similarity 0.994; the only difference was a caption rendered in italics with a rule vs plain; direct render kept |
| 20 scanned pages (Mitteilungsblatt 118–137), `OCR_CONCURRENCY=1` | 172.9 s |
| Same, `OCR_CONCURRENCY=4` (default) | 60.3 s |
| One dense table page (p. 120) alone, prompt / completion tokens | 2,894 / 1,193, about 16 s |

## Licence

MIT, see [LICENSE](LICENSE).
