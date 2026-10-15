from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)


class Metrics:
    """One registry per instance so tests never collide on metric names."""

    content_type = CONTENT_TYPE_LATEST

    def __init__(self, registry: CollectorRegistry | None = None):
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.documents = Counter(
            "ocr_documents_total", "Documents processed, by outcome", ["status"], registry=r
        )
        self.pages = Counter(
            "ocr_pages_total", "Pages processed, by source", ["source"], registry=r
        )
        self.truncated_pages = Counter(
            "ocr_truncated_pages_total", "OCR pages cut off at max_tokens", registry=r
        )
        self.errors = Counter(
            "ocr_errors_total", "Failed requests, by error type", ["type"], registry=r
        )
        self.page_seconds = Histogram(
            "ocr_page_seconds",
            "Per-page processing time, by source",
            ["source"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 180),
            registry=r,
        )
        self.document_seconds = Histogram(
            "ocr_document_seconds",
            "Per-document processing time",
            buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1200),
            registry=r,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)
