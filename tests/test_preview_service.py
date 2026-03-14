from __future__ import annotations

from dataclasses import dataclass

from pdf_merge_gui.services.preview_service import PreviewService
from pdf_merge_gui.services.telemetry import Telemetry


@dataclass
class _FakeRendered:
    w: int
    h: int


class _FakePhotoImage:
    def __init__(self, image: _FakeRendered) -> None:
        self._w = image.w
        self._h = image.h

    def width(self) -> int:
        return self._w

    def height(self) -> int:
        return self._h


def test_preview_service_quantizes_zoom_for_cache_key(monkeypatch) -> None:
    telemetry = Telemetry(enabled=True)
    calls: list[float] = []

    monkeypatch.setattr("pdf_merge_gui.services.preview_service.get_telemetry", lambda: telemetry)
    def _fake_render_page(_source: str, _index: int, zoom: float, quality_tier: str) -> _FakeRendered:
        calls.append(zoom)
        return _FakeRendered(100, 100)

    monkeypatch.setattr("pdf_merge_gui.services.preview_service.render_page", _fake_render_page)
    monkeypatch.setattr("pdf_merge_gui.services.preview_service.ImageTk.PhotoImage", _FakePhotoImage)

    service = PreviewService(cache_size=10)
    first = service.render("a.pdf", 0, 1.03, mode="single")
    second = service.render("a.pdf", 0, 1.04, mode="single")

    assert first is second
    assert calls == [1.0]
    assert telemetry.get_count("preview_cache_miss", tags={"mode": "single", "zoom_bucket": "1.00"}) == 1
    assert telemetry.get_count("preview_cache_hit", tags={"mode": "single", "zoom_bucket": "1.00"}) == 1


def test_preview_service_tracks_eviction_metrics_by_reason(monkeypatch) -> None:
    telemetry = Telemetry(enabled=True)
    monkeypatch.setattr("pdf_merge_gui.services.preview_service.get_telemetry", lambda: telemetry)
    monkeypatch.setattr(
        "pdf_merge_gui.services.preview_service.render_page",
        lambda _source, _index, zoom, quality_tier: _FakeRendered(1000, 1000),
    )
    monkeypatch.setattr("pdf_merge_gui.services.preview_service.ImageTk.PhotoImage", _FakePhotoImage)

    service = PreviewService(cache_size=10, max_pixel_cost=7_000_000)
    service.render("a.pdf", 0, 1.0, mode="final")
    service.render("a.pdf", 1, 1.0, mode="final")

    assert telemetry.get_count("preview_cache_eviction", tags={"reason": "memory"}) == 1


@dataclass
class _FakePropertyImage:
    width: int
    height: int


def test_preview_service_accepts_property_dimensions_for_cost(monkeypatch) -> None:
    monkeypatch.setattr(
        "pdf_merge_gui.services.preview_service.render_page",
        lambda _source, _index, zoom, quality_tier: _FakePropertyImage(width=12, height=8),
    )
    monkeypatch.setattr("pdf_merge_gui.services.preview_service.ImageTk.PhotoImage", lambda image: image)

    service = PreviewService(cache_size=10, max_pixel_cost=10_000)
    rendered = service.render("a.pdf", 0, 1.0, mode="single")

    assert rendered.width == 12
    assert rendered.height == 8
    assert service.cache.total_cost == 12 * 8 * 4
