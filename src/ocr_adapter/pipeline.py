"""Per-page decision, fan-out to vLLM, assembly in page order.

pdfium is not thread-safe, and asyncio cancellation does not stop a thread that
is already inside a pdfium call. So every pdfium call (open, decide, extract,
render, close) is queued on one single-thread executor: the queue serialises
them process-wide, and a close submitted after a render can never overtake it,
even when the task that awaited the render was cancelled meanwhile.

`_slots` is the global concurrency cap. A slot is held from rendering through
the vLLM call, so at most `concurrency` rendered pages exist at once."""

import asyncio
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import pypdfium2 as pdfium
from PIL import Image

from ocr_adapter.config import Settings
from ocr_adapter.documents import Dimensions, Document, Page, open_document
from ocr_adapter.errors import AdapterError, DocumentTimeout, Unprocessable
from ocr_adapter.metrics import Metrics
from ocr_adapter.ocr_client import OcrClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PageResult:
    index: int
    markdown: str
    source: str  # "text" or "ocr"
    dimensions: Dimensions
    truncated: bool = False


@dataclass(frozen=True)
class OcrResult:
    model: str
    pages: list[PageResult]
    seconds: float


def _first_adapter_error(group: BaseExceptionGroup) -> AdapterError | None:
    for exc in group.exceptions:
        if isinstance(exc, AdapterError):
            return exc
        if isinstance(exc, BaseExceptionGroup):
            found = _first_adapter_error(exc)
            if found is not None:
                return found
    return None


class Pipeline:
    def __init__(self, client: OcrClient, settings: Settings, metrics: Metrics | None = None):
        self._client = client
        self._s = settings
        self.metrics = metrics or Metrics()
        self._slots = asyncio.Semaphore(settings.concurrency)
        self._pdfium = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdfium")

    async def _in_pdfium(self, fn: Callable[..., Any], *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._pdfium, fn, *args)
        except pdfium.PdfiumError as exc:
            raise Unprocessable(f"cannot read document: {exc}") from exc

    async def open(self, data: bytes, content_type: str) -> Document:
        return await self._in_pdfium(
            open_document, data, content_type, self._s.max_image_pixels, self._s.max_pages
        )

    async def close(self, document: Document) -> None:
        """Queued behind any pdfium work still running for this document."""
        await self._in_pdfium(document.close)

    async def process(self, document: Document) -> OcrResult:
        started = time.monotonic()
        results: dict[int, PageResult] = {}

        async def run(page: Page) -> None:
            results[page.index] = await self._page(page)

        try:
            async with asyncio.timeout(self._s.document_timeout_s):
                async with asyncio.TaskGroup() as group:
                    for page in document.pages:
                        group.create_task(run(page))
        except TimeoutError:
            self.metrics.documents.labels(status="timeout").inc()
            raise DocumentTimeout(
                f"document not finished within {self._s.document_timeout_s:.0f}s"
            ) from None
        except BaseExceptionGroup as group:
            first = _first_adapter_error(group)
            if first is None:
                raise
            self.metrics.documents.labels(status=first.type).inc()
            raise first from group

        pages = [results[i] for i in sorted(results)]
        seconds = time.monotonic() - started
        ocr_pages = sum(p.source == "ocr" for p in pages)
        self.metrics.documents.labels(status="ok").inc()
        self.metrics.document_seconds.observe(seconds)
        logger.info(
            "document pages=%d ocr=%d text=%d truncated=%d seconds=%.1f",
            len(pages),
            ocr_pages,
            len(pages) - ocr_pages,
            sum(p.truncated for p in pages),
            seconds,
        )
        return OcrResult(model=self._s.model, pages=pages, seconds=seconds)

    # The two functions below run on the pdfium executor thread.

    def _decide(self, page: Page) -> tuple[bool, str, Dimensions]:
        dimensions = page.dimensions(self._s.longest_edge)
        if page.needs_ocr(self._s.min_visible_chars, self._s.image_coverage):
            return True, "", dimensions
        return False, page.text(), dimensions

    def _render(self, page: Page) -> tuple[Image.Image, Dimensions]:
        return page.render(self._s.longest_edge), page.dimensions(self._s.longest_edge)

    async def _page(self, page: Page) -> PageResult:
        started = time.monotonic()
        needs_ocr, text, dimensions = await self._in_pdfium(self._decide, page)
        if not needs_ocr:
            result = PageResult(page.index, text, "text", dimensions)
            self._observe(result, started)
            return result
        async with self._slots:
            image, dimensions = await self._in_pdfium(self._render, page)
            transcription = await self._client.transcribe(image)
        result = PageResult(
            page.index, transcription.text, "ocr", dimensions, transcription.truncated
        )
        self._observe(result, started)
        return result

    def _observe(self, result: PageResult, started: float) -> None:
        self.metrics.pages.labels(source=result.source).inc()
        self.metrics.page_seconds.labels(source=result.source).observe(time.monotonic() - started)
        if result.truncated:
            self.metrics.truncated_pages.inc()
            logger.warning("page %d truncated at max_tokens", result.index)
