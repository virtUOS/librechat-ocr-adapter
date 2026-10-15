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

## Configuration

All settings are environment variables with the prefix `OCR_`; see `src/ocr_adapter/config.py`.
`OCR_PUBLIC_URL` is required (the public base URL that file URLs are built from).
