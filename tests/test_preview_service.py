from __future__ import annotations

from types import SimpleNamespace

from pdf_merge_gui.services import preview_service as module


class DummyTelemetry:
    def __init__(self) -> None:
        self.counts: list[str] = []

    def increment(self, name: str, tags=None) -> None:
        self.counts.append(name)

    def time_block(self, _name: str, tags=None):
        class _Ctx:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Ctx()


def test_preview_service_logs_cache_hit_and_miss(monkeypatch, caplog):
    telemetry = DummyTelemetry()
    monkeypatch.setattr(module, "get_telemetry", lambda: telemetry)

    render_calls: list[tuple[str, int, float]] = []

    def fake_render_page(source_path: str, page_index: int, zoom: float):
        render_calls.append((source_path, page_index, zoom))
        return SimpleNamespace()

    monkeypatch.setattr(module, "render_page", fake_render_page)
    monkeypatch.setattr(module.ImageTk, "PhotoImage", lambda image: f"photo-{len(render_calls)}")

    service = module.PreviewService(cache_size=2)

    with caplog.at_level("DEBUG"):
        first = service.render("doc.pdf", 0, 1.5)
        second = service.render("doc.pdf", 0, 1.5)

    assert first == "photo-1"
    assert second == "photo-1"
    assert render_calls == [("doc.pdf", 0, 1.5)]
    assert telemetry.counts == ["preview_cache_miss", "preview_cache_hit"]
    assert "Preview cache miss for doc.pdf page=0 zoom=1.50" in caplog.text
    assert "Preview cache hit for doc.pdf page=0 zoom=1.50" in caplog.text


def test_preview_service_dimensions_cached(monkeypatch, caplog):
    telemetry = DummyTelemetry()
    monkeypatch.setattr(module, "get_telemetry", lambda: telemetry)

    dimension_calls: list[tuple[str, int]] = []

    def fake_get_page_box_dimensions(source_path: str, page_index: int):
        dimension_calls.append((source_path, page_index))
        return (612.0, 792.0)

    monkeypatch.setattr(module, "get_page_box_dimensions", fake_get_page_box_dimensions)
    service = module.PreviewService(cache_size=2)

    with caplog.at_level("DEBUG"):
        first = service.get_page_dimensions("doc.pdf", 0)
        second = service.get_page_dimensions("doc.pdf", 0)

    assert first == (612.0, 792.0)
    assert second == (612.0, 792.0)
    assert dimension_calls == [("doc.pdf", 0)]
    assert telemetry.counts == ["preview_dimensions_cache_miss", "preview_dimensions_cache_hit"]
    assert "Preview dimensions cache miss for doc.pdf page=0" in caplog.text
    assert "Preview dimensions cache hit for doc.pdf page=0" in caplog.text


def test_preview_render_cache_key_remains_source_page_zoom(monkeypatch):
    monkeypatch.setattr(module, "get_telemetry", lambda: DummyTelemetry())
    monkeypatch.setattr(module, "render_page", lambda source_path, page_index, zoom: SimpleNamespace())
    monkeypatch.setattr(module.ImageTk, "PhotoImage", lambda image: "photo")

    service = module.PreviewService(cache_size=5)
    service.render("doc.pdf", 1, 1.0)
    service.render("doc.pdf", 1, 1.2)

    assert list(service.cache._cache.keys()) == [
        ("doc.pdf", 1, 1.0),
        ("doc.pdf", 1, 1.2),
    ]
