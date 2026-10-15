"""The Mistral-OCR-compatible HTTP surface. Routes are thin: they validate,
resolve the document bytes, and hand off to the pipeline."""

import asyncio
import base64
import binascii
import logging
import tempfile
import time
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from ocr_adapter.config import Settings
from ocr_adapter.documents import sniff_content_type
from ocr_adapter.errors import AdapterError, InvalidRequest, NotFound, TooLarge
from ocr_adapter.metrics import Metrics
from ocr_adapter.ocr_client import OcrClient
from ocr_adapter.pipeline import OcrResult, Pipeline
from ocr_adapter.store import FileStore

logger = logging.getLogger("ocr_adapter")
SWEEP_INTERVAL_S = 60


class DocumentRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["document_url", "image_url"]
    document_url: str | None = None
    image_url: str | None = None

    @property
    def url(self) -> str:
        value = self.document_url if self.type == "document_url" else self.image_url
        if not value:
            raise InvalidRequest(f"document.{self.type} is missing")
        return value


class OcrRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str | None = None
    document: DocumentRef


def _error(status: int, message: str, type_: str) -> JSONResponse:
    return JSONResponse({"message": message, "type": type_}, status_code=status)


def _decode_data_url(url: str, max_bytes: int) -> bytes:
    header, sep, payload = url.partition(",")
    if not sep or not header.endswith(";base64"):
        raise InvalidRequest("data: URL must be base64 encoded")
    if len(payload) > max_bytes * 4 // 3 + 4:
        raise TooLarge(f"inline document exceeds {max_bytes // (1024 * 1024)} MB")
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidRequest("data: URL is not valid base64") from exc


def _resolve(url: str, settings: Settings, store: FileStore) -> tuple[bytes, str, str]:
    """Return (bytes, content type, id for logs) for a document URL.
    Only this service's own file URLs and data: URLs are accepted."""
    if url.startswith("data:"):
        data = _decode_data_url(url, settings.max_file_bytes)
        return data, sniff_content_type(data), "inline"
    prefix = f"{settings.public_url}/v1/files/"
    suffix = "/content"
    if url.startswith(prefix) and url.endswith(suffix):
        file_id = url[len(prefix) : -len(suffix)]
        stored = store.get(file_id)
        if stored is None:
            raise NotFound(f"file {file_id} not found")
        return store.read(file_id), stored.content_type, file_id
    raise InvalidRequest("document URL must be one of this service's file URLs or a data: URL")


def _ocr_response(result: OcrResult, size: int) -> dict:
    return {
        "model": result.model,
        "pages": [
            {
                "index": p.index,
                "markdown": p.markdown,
                "images": [],
                "dimensions": {
                    "dpi": p.dimensions.dpi,
                    "width": p.dimensions.width,
                    "height": p.dimensions.height,
                },
                "source": p.source,
            }
            for p in result.pages
        ],
        "usage_info": {"pages_processed": len(result.pages), "doc_size_bytes": size},
    }


def create_app(
    settings: Settings | None = None,
    *,
    http: httpx.AsyncClient | None = None,
    clock=time.time,
) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request at INFO; the health probe to vLLM would flood the log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    metrics = Metrics()
    store = FileStore(settings.upload_dir, settings.file_ttl_s, clock)
    # Starlette spools multipart bodies over 1 MB to tempfile.gettempdir(). Point
    # that into the upload tmpfs so request bodies never touch the host disk.
    spool = settings.upload_dir / "spool"
    spool.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(spool)
    http = http or httpx.AsyncClient(base_url=settings.vllm_url)
    client = OcrClient(
        http,
        model=settings.model,
        max_tokens=settings.max_tokens,
        temperature=settings.temperature,
        top_p=settings.top_p,
        page_timeout_s=settings.page_timeout_s,
    )
    pipeline = Pipeline(client, settings, metrics)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async def sweep_forever():
            while True:
                await asyncio.sleep(SWEEP_INTERVAL_S)
                store.sweep()

        sweeper = asyncio.create_task(sweep_forever())
        try:
            yield
        finally:
            sweeper.cancel()
            await http.aclose()

    app = FastAPI(
        title="ocr-adapter", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.store = store
    app.state.settings = settings
    app.state.metrics = metrics

    @app.exception_handler(AdapterError)
    async def adapter_error(_request: Request, exc: AdapterError):
        metrics.errors.labels(type=exc.type).inc()
        return _error(exc.status, exc.message, exc.type)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        message = f"invalid request: {where}: {first.get('msg', 'invalid')}"
        return _error(400, message, "invalid_request")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        type_ = "not_found" if exc.status_code == 404 else "http_error"
        return _error(exc.status_code, str(exc.detail), type_)

    @app.exception_handler(Exception)
    async def unexpected(_request: Request, _exc: Exception):
        logger.exception("unhandled error")
        metrics.errors.labels(type="internal_error").inc()
        return _error(500, "internal error", "internal_error")

    @app.post("/v1/files")
    async def upload(purpose: str = Form(...), file: UploadFile = File(...)):
        if purpose != "ocr":
            raise InvalidRequest("purpose must be 'ocr'")
        data = await file.read(settings.max_file_bytes + 1)
        if len(data) > settings.max_file_bytes:
            raise TooLarge(f"file exceeds {settings.max_file_mb} MB")
        content_type = sniff_content_type(data)
        stored = store.save(data, file.filename or "upload", content_type)
        return {
            "id": stored.id,
            "object": "file",
            "bytes": stored.size,
            "created_at": int(stored.created_at),
            "filename": stored.filename,
            "purpose": "ocr",
        }

    @app.get("/v1/files/{file_id}/url")
    async def file_url(file_id: str, expiry: int | None = None):
        if store.get(file_id) is None:
            raise NotFound(f"file {file_id} not found")
        return {"url": f"{settings.public_url}/v1/files/{file_id}/content"}

    @app.get("/v1/files/{file_id}/content")
    async def file_content(file_id: str):
        stored = store.get(file_id)
        if stored is None:
            raise NotFound(f"file {file_id} not found")
        return Response(stored.path.read_bytes(), media_type=stored.content_type)

    @app.delete("/v1/files/{file_id}")
    async def delete_file(file_id: str):
        if not store.delete(file_id):
            raise NotFound(f"file {file_id} not found")
        return {"id": file_id, "object": "file", "deleted": True}

    @app.post("/v1/ocr")
    async def ocr(body: OcrRequest, request: Request):
        data, content_type, source_id = _resolve(body.document.url, settings, store)
        document = await pipeline.open(data, content_type)
        try:
            # A client that hangs up (LibreChat's own timeout, a closed tab)
            # must not keep vLLM busy on its pages.
            result = await pipeline.process(document, disconnected=request.is_disconnected)
        finally:
            await pipeline.close(document)
        logger.info("ocr id=%s pages=%d seconds=%.1f", source_id, len(result.pages), result.seconds)
        return _ocr_response(result, len(data))

    @app.get("/health")
    async def health():
        return {"status": "ok", "vllm": "ok" if await client.healthy() else "unreachable"}

    @app.get("/metrics")
    async def metrics_endpoint():
        return Response(metrics.render(), media_type=Metrics.content_type)

    return app
