from __future__ import annotations

from PIL import Image

from pdf_merge_gui.services.preview_render_executor import PreviewRenderExecutor, PreviewRenderJob


class FakePreviewService:
    def render_pil(self, source_path: str, page_index: int, zoom: float) -> Image.Image:
        size = int(100 * zoom)
        return Image.new("RGB", (size, size + page_index), color=(255, 255, 255))


def test_executor_returns_pil_result_with_metadata() -> None:
    executor = PreviewRenderExecutor(FakePreviewService(), max_workers=1)
    try:
        job = PreviewRenderJob(
            token=7,
            page_slot=3,
            source_path="sample.pdf",
            page_index=5,
            zoom=1.2,
        )
        result = executor.submit(job).result(timeout=2)
    finally:
        executor.shutdown()

    assert result.token == 7
    assert result.page_slot == 3
    assert result.source_path == "sample.pdf"
    assert result.page_index == 5
    assert result.zoom == 1.2
    assert isinstance(result.image, Image.Image)
    assert result.image.size == (120, 125)
