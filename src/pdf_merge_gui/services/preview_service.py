from __future__ import annotations

from typing import Literal

from PIL import ImageTk

from ..preview import render_page
from ..utils.cache import LRUCache
from .telemetry import get_telemetry


class PreviewService:
    def __init__(self, cache_size: int = 100) -> None:
        self.cache: LRUCache[tuple[str, int, float, str], ImageTk.PhotoImage] = LRUCache(cache_size)

    def clear(self) -> None:
        self.cache.clear()

    def clear_for_source(self, source_path: str) -> None:
        self.cache.remove_matching_prefix(source_path)

    def render(
        self,
        source_path: str,
        page_index: int,
        zoom: float,
        quality_tier: Literal["draft", "focus"] = "focus",
    ) -> ImageTk.PhotoImage:
        telemetry = get_telemetry()
        key = (source_path, page_index, zoom, quality_tier)
        cached = self.cache.get(key)
        if cached is not None:
            telemetry.increment("preview_cache_hit")
            return cached

        telemetry.increment("preview_cache_miss")
        with telemetry.time_block("preview_render_miss"):
            image = render_page(source_path, page_index, zoom=zoom, quality_tier=quality_tier)
            photo = ImageTk.PhotoImage(image)
        self.cache.put(key, photo)
        return photo
