import asyncio
import time

import pytest

from ocr_adapter.documents import PdfDocument
from ocr_adapter.errors import DocumentTimeout, TooLarge, UpstreamError
from ocr_adapter.ocr_client import OcrClient
from ocr_adapter.pipeline import Pipeline

from fixtures import make_image, make_pdf


def make_pipeline(settings, vllm_http, **overrides) -> Pipeline:
    s = settings.model_copy(update=overrides)
    client = OcrClient(
        vllm_http,
        model=s.model,
        max_tokens=s.max_tokens,
        temperature=s.temperature,
        top_p=s.top_p,
        page_timeout_s=s.page_timeout_s,
    )
    return Pipeline(client, s)


def expected_ocr_text(fake, data: bytes, index: int, longest_edge: int) -> str:
    page = PdfDocument(data).pages[index]
    return fake.text_for(OcrClient.encode_png(page.render(longest_edge)))


async def run(pipeline, data, content_type="application/pdf"):
    document = await pipeline.open(data, content_type)
    try:
        return await pipeline.process(document)
    finally:
        await pipeline.close(document)


async def test_routes_pages_by_source(settings, fake, vllm_http):
    data = make_pdf(["text", "scan", "mixed", "invisible"])
    result = await run(make_pipeline(settings, vllm_http), data)
    assert [p.source for p in result.pages] == ["text", "ocr", "text", "ocr"]
    assert [p.index for p in result.pages] == [0, 1, 2, 3]
    assert "Absatz 0:" in result.pages[0].markdown
    assert result.pages[1].markdown == expected_ocr_text(fake, data, 1, settings.longest_edge)
    assert result.pages[3].markdown == expected_ocr_text(fake, data, 3, settings.longest_edge)
    assert len(fake.requests) == 2
    assert result.model == settings.model
    assert result.pages[1].dimensions.width > 0
    assert result.seconds >= 0


async def test_text_only_document_makes_no_vllm_call(settings, fake, vllm_http):
    result = await run(make_pipeline(settings, vllm_http), make_pdf(["text", "text"]))
    assert fake.requests == []
    assert all(p.source == "text" for p in result.pages)


async def test_page_order_survives_out_of_order_completion(settings, fake, vllm_http):
    data = make_pdf(["scan", "scan", "scan", "scan"])
    fake.delays = [0.3, 0.0, 0.0, 0.0]  # the first page finishes last
    result = await run(make_pipeline(settings, vllm_http), data)
    assert [p.index for p in result.pages] == [0, 1, 2, 3]
    for i in range(4):
        assert result.pages[i].markdown == expected_ocr_text(fake, data, i, settings.longest_edge)


async def test_concurrency_cap(settings, fake, vllm_http):
    fake.delay = 0.05
    await run(make_pipeline(settings, vllm_http, concurrency=2), make_pdf(["scan"] * 5))
    assert len(fake.requests) == 5
    assert fake.max_in_flight == 2


async def test_two_documents_share_the_cap(settings, fake, vllm_http):
    fake.delay = 0.05
    pipeline = make_pipeline(settings, vllm_http, concurrency=2)
    a, b = await asyncio.gather(
        run(pipeline, make_pdf(["scan"] * 3)),
        run(pipeline, make_pdf(["scan"] * 3)),
    )
    assert len(a.pages) == 3 and len(b.pages) == 3
    assert fake.max_in_flight == 2
    assert len(fake.requests) == 6


async def test_image_document_is_one_ocr_page(settings, fake, vllm_http):
    result = await run(make_pipeline(settings, vllm_http), make_image("PNG"), "image/png")
    assert len(result.pages) == 1 and result.pages[0].source == "ocr"


async def test_open_rejects_too_many_pages(settings, vllm_http):
    pipeline = make_pipeline(settings, vllm_http, max_pages=2)
    with pytest.raises(TooLarge):
        await pipeline.open(make_pdf(["text"] * 3), "application/pdf")


async def test_page_failure_fails_document_and_cancels_the_rest(settings, fake, vllm_http):
    fake.fail_times = 999
    fake.delay = 0.2
    started = time.monotonic()
    with pytest.raises(UpstreamError):
        await run(make_pipeline(settings, vllm_http, concurrency=2), make_pdf(["scan"] * 5))
    elapsed = time.monotonic() - started
    # Without cancellation all 5 pages would be tried twice (10 requests).
    assert len(fake.requests) < 10
    assert elapsed < 0.9
    await asyncio.sleep(0.05)
    assert fake.in_flight == 0


async def test_document_deadline(settings, fake, vllm_http):
    fake.delay = 1.0
    started = time.monotonic()
    with pytest.raises(DocumentTimeout):
        await run(
            make_pipeline(settings, vllm_http, document_timeout_s=0.2), make_pdf(["scan", "scan"])
        )
    assert time.monotonic() - started < 0.6
    await asyncio.sleep(0.05)
    assert fake.in_flight == 0


async def test_truncation_is_reported_and_counted(settings, fake, vllm_http):
    fake.truncate = True
    pipeline = make_pipeline(settings, vllm_http)
    result = await run(pipeline, make_pdf(["scan"]))
    assert result.pages[0].truncated is True
    text = pipeline.metrics.render().decode()
    assert "ocr_truncated_pages_total 1.0" in text
    assert 'ocr_pages_total{source="ocr"} 1.0' in text
    assert 'ocr_documents_total{status="ok"} 1.0' in text
