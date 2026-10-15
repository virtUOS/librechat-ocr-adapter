"""An in-process stand-in for vLLM's OpenAI chat-completions endpoint.

The returned text is a hash of the image data URL, so a test can predict the
exact output for a page by rendering it the same way the adapter does."""

import asyncio
import hashlib

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class FakeVllm:
    def __init__(self):
        self.app = FastAPI()
        self.requests: list[dict] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.delay = 0.0
        self.delays: list[float] = []
        self.fail_times = 0
        self.fail_status = 500
        self.truncate = False
        self.content_as_list = False
        self.app.post("/v1/chat/completions")(self._completions)
        self.app.get("/health")(self._health)

    @staticmethod
    def text_for(url: str) -> str:
        return "ocr:" + hashlib.sha256(url.encode()).hexdigest()[:12]

    async def _completions(self, request: Request):
        body = await request.json()
        self.requests.append(body)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            delay = self.delays.pop(0) if self.delays else self.delay
            if delay:
                await asyncio.sleep(delay)
            if self.fail_times > 0:
                self.fail_times -= 1
                return JSONResponse({"error": {"message": "boom"}}, status_code=self.fail_status)
            url = body["messages"][0]["content"][0]["image_url"]["url"]
            text = self.text_for(url)
            content = [{"type": "text", "text": text}] if self.content_as_list else text
            return {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "model": body.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "length" if self.truncate else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2894, "completion_tokens": 942, "total_tokens": 3836},
            }
        finally:
            self.in_flight -= 1

    async def _health(self):
        return {"status": "ok"}
