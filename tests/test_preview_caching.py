from __future__ import annotations

from pathlib import Path

from PIL import Image

from pdf_merge_gui.preview import DocumentHandleCache, SourceFingerprint
from pdf_merge_gui.services.preview_service import PreviewService


def test_preview_service_raster_cache_reuses_decoded_bytes(monkeypatch, tmp_path) -> None:
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"fake")

    fingerprint = SourceFingerprint(path=str(source.resolve()), mtime_ns=1, size=4)
    render_calls: list[tuple[str, int, float]] = []

    monkeypatch.setattr(
        "pdf_merge_gui.services.preview_service.build_source_fingerprint",
        lambda _path: fingerprint,
    )

    def fake_render_page(path: str, page_index: int, zoom: float):
        render_calls.append((path, page_index, zoom))
        return fingerprint, Image.new("RGB", (40, 50), color=(255, 255, 255))

    monkeypatch.setattr("pdf_merge_gui.services.preview_service.render_page", fake_render_page)

    service = PreviewService(cache_size=8, photo_cache_size=4)

    image_one = service.render_pil(str(source), page_index=0, zoom=1.25)
    image_two = service.render_pil(str(source), page_index=0, zoom=1.25)

    assert image_one.size == (40, 50)
    assert image_two.size == (40, 50)
    assert len(render_calls) == 1


def test_preview_service_photo_cache_reuses_tk_image(monkeypatch, tmp_path) -> None:
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"fake")

    fingerprint = SourceFingerprint(path=str(source.resolve()), mtime_ns=1, size=4)
    monkeypatch.setattr(
        "pdf_merge_gui.services.preview_service.build_source_fingerprint",
        lambda _path: fingerprint,
    )

    class FakePhotoImage:
        def __init__(self, image):
            self.image = image

    monkeypatch.setattr("pdf_merge_gui.services.preview_service.ImageTk.PhotoImage", FakePhotoImage)

    render_count = 0

    def fake_render_pil(_source_path: str, _page_index: int, _zoom: float):
        nonlocal render_count
        render_count += 1
        return Image.new("RGB", (30, 30), color=(0, 0, 0))

    service = PreviewService(cache_size=8, photo_cache_size=4)
    monkeypatch.setattr(service, "render_pil", fake_render_pil)

    first = service.render(str(source), 0, 1.0)
    second = service.render(str(source), 0, 1.0)

    assert first is second
    assert render_count == 1


def test_document_handle_cache_reuses_and_invalidates_on_file_change(monkeypatch, tmp_path) -> None:
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"v1")

    open_calls: list[str] = []

    class FakeDocument:
        def __init__(self, tag: str):
            self.tag = tag
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FakeFitz:
        @staticmethod
        def open(path: str):
            open_calls.append(path)
            return FakeDocument(path)

    monkeypatch.setattr("pdf_merge_gui.preview._import_fitz", lambda: FakeFitz)

    cache = DocumentHandleCache(max_open_documents=2)

    _fp1, doc1 = cache.get_document(str(source))
    _fp2, doc2 = cache.get_document(str(source))
    assert doc1 is doc2
    assert len(open_calls) == 1

    source.write_bytes(b"v2-changed")
    _fp3, doc3 = cache.get_document(str(source))
    assert doc3 is not doc2
    assert len(open_calls) == 2
    assert doc2.closed is True

    cache.clear()
    assert doc3.closed is True
