import pytest

from ocr_adapter.errors import NotFound
from ocr_adapter.store import FileStore


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def test_save_get_read_delete(tmp_path):
    store = FileStore(tmp_path, ttl_s=60, clock=Clock())
    f = store.save(b"%PDF-1.7 data", "Prüfungsordnung.pdf", "application/pdf")
    assert len(f.id) == 32 and f.id.isalnum()
    assert f.filename == "Prüfungsordnung.pdf"
    assert f.content_type == "application/pdf"
    assert f.size == 13
    assert f.created_at == 1000.0
    assert store.get(f.id) == f
    assert store.read(f.id) == b"%PDF-1.7 data"
    assert len(store) == 1
    assert store.delete(f.id) is True
    assert store.get(f.id) is None
    assert not f.path.exists()
    assert store.delete(f.id) is False
    with pytest.raises(NotFound):
        store.read(f.id)


def test_ids_are_unique(tmp_path):
    store = FileStore(tmp_path, ttl_s=60, clock=Clock())
    ids = {store.save(b"x", "a", "image/png").id for _ in range(50)}
    assert len(ids) == 50


def test_sweep_removes_expired_only(tmp_path):
    clock = Clock()
    store = FileStore(tmp_path, ttl_s=60, clock=clock)
    old = store.save(b"a", "a", "image/png")
    clock.now += 30
    young = store.save(b"b", "b", "image/png")
    clock.now += 31  # old is 61 s, young is 31 s
    assert store.sweep() == 1
    assert store.get(old.id) is None and not old.path.exists()
    assert store.get(young.id) is not None
    assert store.sweep() == 0


def test_clear_on_startup_removes_stray_files(tmp_path):
    (tmp_path / "leftover").write_bytes(b"old")
    store = FileStore(tmp_path, ttl_s=60, clock=Clock())
    assert not (tmp_path / "leftover").exists()
    assert len(store) == 0


def test_directory_is_created(tmp_path):
    store = FileStore(tmp_path / "nested" / "uploads", ttl_s=60, clock=Clock())
    f = store.save(b"x", "x", "image/png")
    assert f.path.parent == tmp_path / "nested" / "uploads"
