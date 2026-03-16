import sys
from types import SimpleNamespace

import pytest

from pdf_merge_gui.preview import DocumentSessionCache, PreviewRenderError, render_page
from pdf_merge_gui.services.preview_service import PreviewService
from pdf_merge_gui.services.telemetry import Telemetry


class FakePixmap:
    width = 1
    height = 1
    samples = b"\x00\x00\x00"


class FakePage:
    def get_pixmap(self, matrix, alpha=False):
        assert alpha is False
        assert matrix == (1.5, 1.5)
        return FakePixmap()


class FakeDocument:
    def __init__(self, pages=1):
        self.pages = pages
        self.closed = False

    @property
    def is_closed(self):
        return self.closed

    def __len__(self):
        if self.closed:
            raise RuntimeError("closed")
        return self.pages

    def load_page(self, page_index):
        assert 0 <= page_index < self.pages
        return FakePage()

    def close(self):
        self.closed = True


@pytest.fixture
def fake_fitz(monkeypatch):
    opened: list[FakeDocument] = []

    def open_pdf(_path):
        doc = FakeDocument(pages=2)
        opened.append(doc)
        return doc

    module = SimpleNamespace(open=open_pdf, Matrix=lambda x, y: (x, y))
    monkeypatch.setitem(sys.modules, "fitz", module)
    return module, opened


def test_document_session_cache_reuses_and_evicts(fake_fitz, monkeypatch):
    fitz_module, opened = fake_fitz
    cache = DocumentSessionCache(capacity=1)
    monkeypatch.setattr("pdf_merge_gui.preview.Path.exists", lambda _self: True)

    first = cache.get_or_open("a.pdf", fitz_module)
    again = cache.get_or_open("a.pdf", fitz_module)

    assert first is again
    assert len(opened) == 1

    cache.get_or_open("b.pdf", fitz_module)
    assert len(opened) == 2
    assert first.closed is True


def test_render_page_retries_after_invalid_cached_document(fake_fitz, monkeypatch):
    fitz_module, opened = fake_fitz
    cache = DocumentSessionCache(capacity=2)

    stale = FakeDocument(pages=2)
    stale.closed = True
    cache._cache["a.pdf"] = stale

    monkeypatch.setattr("pdf_merge_gui.preview.Path.exists", lambda _self: True)

    image = render_page("a.pdf", 0, zoom=1.5, document_cache=cache)

    assert image.size == (1, 1)
    assert len(opened) == 1


def test_render_page_reports_missing_file(fake_fitz, monkeypatch):
    _fitz_module, _opened = fake_fitz
    cache = DocumentSessionCache(capacity=2)
    monkeypatch.setattr("pdf_merge_gui.preview.Path.exists", lambda _self: False)

    with pytest.raises(PreviewRenderError):
        render_page("missing.pdf", 0, zoom=1.5, document_cache=cache)


def test_preview_service_document_cache_configuration():
    service = PreviewService(cache_size=4, document_cache_size=3)

    assert service.cache.capacity == 4
    assert service.document_cache.capacity == 3


def test_preview_service_quantizes_zoom_for_cache_and_render(monkeypatch):
    service = PreviewService(cache_size=4, document_cache_size=3)
    telemetry = Telemetry(enabled=True)
    render_calls: list[float] = []

    monkeypatch.setattr('pdf_merge_gui.services.preview_service.get_telemetry', lambda: telemetry)

    class FakePhotoImage:
        def __init__(self, image):
            self.image = image

    monkeypatch.setattr('pdf_merge_gui.services.preview_service.ImageTk.PhotoImage', FakePhotoImage)

    def fake_render_page(_source_path, _page_index, zoom, document_cache, rotation_degrees=0):
        assert document_cache is service.document_cache
        assert rotation_degrees == 0
        render_calls.append(zoom)
        return f'rendered:{zoom}'

    monkeypatch.setattr('pdf_merge_gui.services.preview_service.render_page', fake_render_page)

    first = service.render('a.pdf', 0, 1.233)
    second = service.render('a.pdf', 0, 1.234)

    assert first is second
    assert render_calls == [1.23]
    assert telemetry.get_count('preview_cache_miss') == 1
    assert telemetry.get_count('preview_cache_hit') == 1
    assert telemetry.get_count('zoom_quantized_miss') == 1
    assert telemetry.get_count('zoom_quantized_hit') == 1


def test_preview_service_quantize_zoom_rounds_to_two_decimals():
    assert PreviewService._quantize_zoom(1.234) == 1.23
    assert PreviewService._quantize_zoom(1.236) == 1.24
