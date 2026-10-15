from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All settings come from environment variables prefixed OCR_."""

    model_config = SettingsConfigDict(env_prefix="OCR_", extra="ignore")

    vllm_url: str = "http://vllm:8000"
    model: str = "lightonai/LightOnOCR-3-4B"
    public_url: str
    concurrency: int = 4
    max_tokens: int = 4096
    temperature: float = 0.2
    top_p: float = 0.9
    page_timeout_s: float = 180
    document_timeout_s: float = 1200
    max_file_mb: int = 100
    max_pages: int = 100
    file_ttl_s: float = 3600
    upload_dir: Path = Path("/uploads")
    min_visible_chars: int = 200
    image_coverage: float = 0.5
    longest_edge: int = 2048
    log_level: str = "info"

    @field_validator("public_url", "vllm_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024
