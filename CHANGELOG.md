# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-16

First public release: the service as it has run in production for the LibreChat instances of
Osnabrück University since 2026-10-16.

### Added

- The four Mistral OCR API calls LibreChat v0.8.8 makes: `POST /v1/files`,
  `GET /v1/files/{id}/url`, `POST /v1/ocr`, `DELETE /v1/files/{id}`, plus `GET /health`.
- Per-page decision: pages with a usable visible text layer are extracted directly, scanned
  pages and images are rendered and transcribed by `lightonai/LightOnOCR-3-4B` on vLLM; the
  `source` field on each page says which.
- Image documents with EXIF orientation, alpha and multi-frame TIFF handling; bounded image
  memory; 422 on broken PDFs.
- Uploads spooled on a tmpfs with a TTL sweep; OCR results are never stored.
- Prometheus metrics at `GET /metrics`.
- The `librechat-ocr` CLI, replaying LibreChat's upload sequence against a deployed service.
- Container image `ghcr.io/virtuos/librechat-ocr-adapter` for `linux/amd64` and `linux/arm64`,
  installed from `uv.lock`.

[0.1.0]: https://github.com/virtUOS/librechat-ocr-adapter/releases/tag/v0.1.0
