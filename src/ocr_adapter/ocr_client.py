"""One call to vLLM per page image. No concurrency control here; the pipeline
holds the global slot across rendering and this call."""

import asyncio
import base64
import io
import logging
from dataclasses import dataclass

import httpx
from PIL import Image

from ocr_adapter.errors import UpstreamError, UpstreamUnavailable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Transcription:
    text: str
    truncated: bool


class OcrClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        page_timeout_s: float,
        retries: int = 1,
    ):
        self._http = http
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._top_p = top_p
        self._page_timeout_s = page_timeout_s
        self._retries = retries

    @staticmethod
    def encode_png(image: Image.Image) -> str:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    def payload(self, image: Image.Image) -> dict:
        # The image alone, with no text part: that is what LightOnOCR was trained on.
        return {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": self.encode_png(image)}}
                    ],
                }
            ],
            "temperature": self._temperature,
            "top_p": self._top_p,
            "max_tokens": self._max_tokens,
        }

    async def transcribe(self, image: Image.Image) -> Transcription:
        payload = self.payload(image)
        attempts = self._retries + 1
        error: UpstreamError | UpstreamUnavailable = UpstreamError("no attempt made")
        for attempt in range(1, attempts + 1):
            try:
                # asyncio.wait_for enforces the deadline for every transport,
                # including ASGI transports that ignore httpx timeouts.
                response = await asyncio.wait_for(
                    self._http.post(
                        "/v1/chat/completions",
                        json=payload,
                        timeout=httpx.Timeout(self._page_timeout_s, connect=10.0),
                    ),
                    timeout=self._page_timeout_s,
                )
                response.raise_for_status()
                return self._parse(response.json())
            except httpx.ConnectError as exc:
                error = UpstreamUnavailable(f"vLLM unreachable: {exc}")
            except (httpx.TimeoutException, TimeoutError):
                error = UpstreamError(f"vLLM did not answer within {self._page_timeout_s:.0f}s")
            except httpx.HTTPStatusError as exc:
                error = UpstreamError(f"vLLM returned HTTP {exc.response.status_code}")
            except httpx.HTTPError as exc:
                error = UpstreamError(f"vLLM request failed: {exc}")
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                error = UpstreamError(f"malformed vLLM response: {exc}")
            logger.warning("vLLM attempt %d/%d failed: %s", attempt, attempts, error.message)
        raise error

    @staticmethod
    def _parse(body: dict) -> Transcription:
        choice = body["choices"][0]
        content = choice["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise ValueError("content is not text")
        return Transcription(
            text=content.strip(), truncated=choice.get("finish_reason") == "length"
        )

    async def healthy(self) -> bool:
        try:
            response = await self._http.get("/health", timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200
