import base64
import io

import httpx
import pytest
from PIL import Image

from ocr_adapter.errors import UpstreamError, UpstreamUnavailable
from ocr_adapter.ocr_client import OcrClient

IMAGE = Image.new("RGB", (64, 48), (10, 20, 30))


def make_client(http, **overrides):
    kwargs = dict(
        model="lightonai/LightOnOCR-3-4B",
        max_tokens=4096,
        temperature=0.2,
        top_p=0.9,
        page_timeout_s=1.0,
    )
    kwargs.update(overrides)
    return OcrClient(http, **kwargs)


async def test_request_shape(fake, vllm_http):
    result = await make_client(vllm_http).transcribe(OcrClient.encode_png(IMAGE))
    body = fake.requests[-1]
    assert body["model"] == "lightonai/LightOnOCR-3-4B"
    assert body["temperature"] == 0.2
    assert body["top_p"] == 0.9
    assert body["max_tokens"] == 4096
    assert len(body["messages"]) == 1 and body["messages"][0]["role"] == "user"
    content = body["messages"][0]["content"]
    assert len(content) == 1  # the image only, no text part
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert result.text == fake.text_for(content[0]["image_url"]["url"])
    assert result.truncated is False


def test_encode_png_round_trips():
    url = OcrClient.encode_png(IMAGE)
    data = base64.b64decode(url.split(",", 1)[1])
    assert Image.open(io.BytesIO(data)).size == (64, 48)


async def test_retries_once_then_succeeds(fake, vllm_http):
    fake.fail_times = 1
    await make_client(vllm_http).transcribe(OcrClient.encode_png(IMAGE))
    assert len(fake.requests) == 2


async def test_fails_after_one_retry(fake, vllm_http):
    fake.fail_times = 2
    with pytest.raises(UpstreamError):
        await make_client(vllm_http).transcribe(OcrClient.encode_png(IMAGE))
    assert len(fake.requests) == 2


async def test_timeout_is_upstream_error(fake, vllm_http):
    fake.delay = 0.5
    with pytest.raises(UpstreamError):
        await make_client(vllm_http, page_timeout_s=0.1).transcribe(OcrClient.encode_png(IMAGE))
    assert len(fake.requests) == 2


async def test_connection_failure_is_unavailable():
    calls = 0

    def refuse(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection refused", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://vllm")
    with pytest.raises(UpstreamUnavailable):
        await make_client(http).transcribe(OcrClient.encode_png(IMAGE))
    assert calls == 2
    assert await make_client(http).healthy() is False


async def test_truncation_is_flagged(fake, vllm_http):
    fake.truncate = True
    assert (await make_client(vllm_http).transcribe(OcrClient.encode_png(IMAGE))).truncated is True


async def test_list_content_is_joined(fake, vllm_http):
    fake.content_as_list = True
    result = await make_client(vllm_http).transcribe(OcrClient.encode_png(IMAGE))
    assert result.text.startswith("ocr:")


async def test_malformed_response_is_upstream_error():
    def weird(request):
        return httpx.Response(200, json={"choices": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(weird), base_url="http://vllm")
    with pytest.raises(UpstreamError):
        await make_client(http).transcribe(OcrClient.encode_png(IMAGE))


async def test_healthy(fake, vllm_http):
    assert await make_client(vllm_http).healthy() is True


async def test_transcribe_takes_a_data_url(fake, vllm_http):
    url = OcrClient.encode_png(IMAGE)
    result = await make_client(vllm_http).transcribe(url)
    assert fake.requests[-1]["messages"][0]["content"][0]["image_url"]["url"] == url
    assert result.text == fake.text_for(url)
