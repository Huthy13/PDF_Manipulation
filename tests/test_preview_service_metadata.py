from __future__ import annotations

from pdf_merge_gui.services.preview_service import PreviewService


def test_get_page_dimensions_uses_metadata_cache(monkeypatch) -> None:
    service = PreviewService()
    calls: list[tuple[str, int]] = []

    def fake_read(path: str, page_index: int) -> tuple[float, float]:
        calls.append((path, page_index))
        return (612.0, 792.0)

    monkeypatch.setattr("pdf_merge_gui.services.preview_service.read_page_dimensions", fake_read)

    first = service.get_page_dimensions("a.pdf", 0)
    second = service.get_page_dimensions("a.pdf", 0)

    assert first == (612.0, 792.0)
    assert second == (612.0, 792.0)
    assert calls == [("a.pdf", 0)]


def test_clear_for_source_removes_dimension_metadata(monkeypatch) -> None:
    service = PreviewService()
    calls: list[tuple[str, int]] = []

    def fake_read(path: str, page_index: int) -> tuple[float, float]:
        calls.append((path, page_index))
        return (300.0 + page_index, 400.0 + page_index)

    monkeypatch.setattr("pdf_merge_gui.services.preview_service.read_page_dimensions", fake_read)

    assert service.get_page_dimensions("a.pdf", 1) == (301.0, 401.0)
    service.clear_for_source("a.pdf")
    assert service.get_page_dimensions("a.pdf", 1) == (301.0, 401.0)

    assert calls == [("a.pdf", 1), ("a.pdf", 1)]
