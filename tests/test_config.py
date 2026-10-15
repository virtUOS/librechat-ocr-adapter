import pytest
from pydantic import ValidationError

from ocr_adapter.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("OCR_PUBLIC_URL", raising=False)
    s = Settings(public_url="https://ocr.example.org/")
    assert s.public_url == "https://ocr.example.org"  # trailing slash stripped
    assert s.vllm_url == "http://vllm:8000"
    assert s.model == "lightonai/LightOnOCR-3-4B"
    assert s.concurrency == 4
    assert s.max_tokens == 4096
    assert s.temperature == 0.2
    assert s.top_p == 0.9
    assert s.page_timeout_s == 180
    assert s.document_timeout_s == 1200
    assert s.max_file_mb == 100
    assert s.max_file_bytes == 100 * 1024 * 1024
    assert s.max_pages == 100
    assert s.file_ttl_s == 3600
    assert str(s.upload_dir) == "/uploads"
    assert s.min_visible_chars == 200
    assert s.image_coverage == 0.5
    assert s.longest_edge == 2048
    assert s.max_image_pixels == 50_000_000
    assert s.log_level == "info"


def test_env_prefix(monkeypatch):
    monkeypatch.setenv("OCR_PUBLIC_URL", "https://x.example")
    monkeypatch.setenv("OCR_CONCURRENCY", "2")
    s = Settings()
    assert s.public_url == "https://x.example"
    assert s.concurrency == 2


def test_public_url_required(monkeypatch):
    monkeypatch.delenv("OCR_PUBLIC_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()
