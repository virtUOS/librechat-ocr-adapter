import os
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))  # so tests can import fixtures and fake_vllm

from ocr_adapter.config import Settings  # noqa: E402

from fake_vllm import FakeVllm  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("OCR_"):
            monkeypatch.delenv(key)


@pytest.fixture
def fake():
    return FakeVllm()


@pytest.fixture
def vllm_http(fake):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), base_url="http://vllm")


@pytest.fixture
def settings(tmp_path):
    return Settings(
        public_url="http://adapter",
        vllm_url="http://vllm",
        upload_dir=tmp_path / "uploads",
        concurrency=2,
        page_timeout_s=2,
        document_timeout_s=10,
        max_file_mb=1,
        max_pages=5,
        file_ttl_s=3600,
        longest_edge=512,
    )


from ocr_adapter.app import create_app  # noqa: E402


@pytest.fixture
def app(settings, vllm_http):
    yield create_app(settings, http=vllm_http)
    tempfile.tempdir = None  # create_app points the spool into the upload dir


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter"
    ) as c:
        yield c
