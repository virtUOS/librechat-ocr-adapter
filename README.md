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
