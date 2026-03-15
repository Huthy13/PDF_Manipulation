from __future__ import annotations

from PIL import ImageTk

from ..preview import DocumentSessionCache, render_page
from ..utils.cache import LRUCache
from .telemetry import get_telemetry


class PreviewService:
    ZOOM_QUANTIZATION_DIGITS = 2

    def __init__(self, cache_size: int = 100, document_cache_size: int = 16) -> None:
        self.cache: LRUCache[tuple[str, int, float], ImageTk.PhotoImage] = LRUCache(cache_size)
        self.document_cache = DocumentSessionCache(capacity=document_cache_size)

    @classmethod
    def _quantize_zoom(cls, zoom: float) -> float:
        return round(zoom, cls.ZOOM_QUANTIZATION_DIGITS)

    def clear(self) -> None:
        self.cache.clear()
        self.document_cache.clear()

    def clear_for_source(self, source_path: str) -> None:
        self.cache.remove_matching_prefix(source_path)
        self.document_cache.clear_for_source(source_path)

    def render(self, source_path: str, page_index: int, zoom: float) -> ImageTk.PhotoImage:
        telemetry = get_telemetry()
        quantized_zoom = self._quantize_zoom(zoom)
        key = (source_path, page_index, quantized_zoom)
        cached = self.cache.get(key)
        if cached is not None:
            telemetry.increment("preview_cache_hit")
            telemetry.increment("zoom_quantized_hit")
            return cached

        telemetry.increment("preview_cache_miss")
        telemetry.increment("zoom_quantized_miss")
        with telemetry.time_block("preview_render_miss"):
            image = render_page(source_path, page_index, zoom=quantized_zoom, document_cache=self.document_cache)
            photo = ImageTk.PhotoImage(image)
        self.cache.put(key, photo)
        return photo
