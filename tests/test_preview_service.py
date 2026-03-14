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
