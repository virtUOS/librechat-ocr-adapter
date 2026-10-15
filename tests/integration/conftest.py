import importlib.util
import os
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "ocr.py"
CACHE = Path(__file__).parent / ".cache"
DOCUMENTS = {
    "po": "https://www.uni-osnabrueck.de/fileadmin/uos/10_studieren/20_unser_studienangebot/20_abschluesse/70_ordnungen_studium/Allgemeine-PO-Bachelor-Master_2025-03.pdf",
    "mitteilungsblatt": "https://www.uni-osnabrueck.de/fileadmin/uos/40_campusleben/10_uni-news/30_amtl_mitteilungsblatt/2021-2025/2025-03-20_Nr01.pdf",
    "bundestag": "https://dserver.bundestag.de/btd/01/000/0100001.pdf",
}


def pytest_collection_modifyitems(config, items):
    if os.environ.get("OCR_TEST_BASE_URL") and os.environ.get("OCR_TEST_API_KEY"):
        return
    skip = pytest.mark.skip(reason="set OCR_TEST_BASE_URL and OCR_TEST_API_KEY to run")
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def cli():
    spec = importlib.util.spec_from_file_location("ocr_cli", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def fetch():
    CACHE.mkdir(exist_ok=True)

    def _fetch(name: str) -> bytes:
        path = CACHE / f"{name}.pdf"
        if not path.exists():
            response = httpx.get(DOCUMENTS[name], follow_redirects=True, timeout=120)
            response.raise_for_status()
            path.write_bytes(response.content)
        return path.read_bytes()

    return _fetch


@pytest.fixture(scope="session")
def service():
    base = os.environ["OCR_TEST_BASE_URL"].rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    with httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {os.environ['OCR_TEST_API_KEY']}"},
        timeout=httpx.Timeout(1800, connect=10),
    ) as client:
        yield client
