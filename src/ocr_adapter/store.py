"""Uploads on disk (a tmpfs in production) with metadata in memory.
Nothing survives a restart, and nothing outlives the TTL."""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ocr_adapter.errors import NotFound

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredFile:
    id: str
    filename: str
    content_type: str
    size: int
    created_at: float
    path: Path


class FileStore:
    def __init__(self, directory: Path, ttl_s: float, clock: Callable[[], float] = time.time):
        self._dir = directory
        self._ttl = ttl_s
        self._clock = clock
        self._files: dict[str, StoredFile] = {}
        self._dir.mkdir(parents=True, exist_ok=True)
        self.clear()

    def clear(self) -> None:
        for path in self._dir.iterdir():
            if path.is_file():
                path.unlink()
        self._files.clear()

    def save(self, data: bytes, filename: str, content_type: str) -> StoredFile:
        file_id = uuid.uuid4().hex
        path = self._dir / file_id
        partial = path.with_name(file_id + ".part")
        partial.write_bytes(data)
        partial.replace(path)
        stored = StoredFile(file_id, filename, content_type, len(data), self._clock(), path)
        self._files[file_id] = stored
        logger.info("stored file id=%s bytes=%d type=%s", file_id, len(data), content_type)
        return stored

    def get(self, file_id: str) -> StoredFile | None:
        return self._files.get(file_id)

    def read(self, file_id: str) -> bytes:
        stored = self.get(file_id)
        if stored is None:
            raise NotFound(f"file {file_id} not found")
        return stored.path.read_bytes()

    def delete(self, file_id: str) -> bool:
        stored = self._files.pop(file_id, None)
        if stored is None:
            return False
        stored.path.unlink(missing_ok=True)
        logger.info("deleted file id=%s", file_id)
        return True

    def sweep(self) -> int:
        now = self._clock()
        expired = [fid for fid, f in self._files.items() if now - f.created_at >= self._ttl]
        for fid in expired:
            self.delete(fid)
        if expired:
            logger.info("swept %d expired files", len(expired))
        return len(expired)

    def __len__(self) -> int:
        return len(self._files)
