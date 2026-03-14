from __future__ import annotations

from threading import RLock

from PIL import Image, ImageTk

from ..preview import render_page
from ..utils.cache import LRUCache
from .telemetry import get_telemetry


class PreviewService:
    def __init__(self, cache_size: int = 100) -> None:
        self.cache: LRUCache[tuple[str, int, float], Image.Image] = LRUCache(cache_size)
        self._cache_lock = RLock()

    def clear(self) -> None:
        with self._cache_lock:
            self.cache.clear()

    def clear_for_source(self, source_path: str) -> None:
        with self._cache_lock:
            self.cache.remove_matching_prefix(source_path)

    def render_pil(self, source_path: str, page_index: int, zoom: float) -> Image.Image:
        telemetry = get_telemetry()
        key = (source_path, page_index, zoom)
        with self._cache_lock:
            cached = self.cache.get(key)
        if cached is not None:
            telemetry.increment("preview_cache_hit")
            return cached

        telemetry.increment("preview_cache_miss")
        with telemetry.time_block("preview_render_miss"):
            image = render_page(source_path, page_index, zoom=zoom)
        with self._cache_lock:
            self.cache.put(key, image)
        return image

    def render(self, source_path: str, page_index: int, zoom: float) -> ImageTk.PhotoImage:
        return ImageTk.PhotoImage(self.render_pil(source_path, page_index, zoom))
