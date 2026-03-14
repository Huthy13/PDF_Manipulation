from __future__ import annotations

import logging

from PIL import ImageTk

from ..preview import get_page_box_dimensions, render_page
from ..utils.cache import LRUCache
from .telemetry import get_telemetry


logger = logging.getLogger(__name__)


class PreviewService:
    def __init__(self, cache_size: int = 100) -> None:
        self.cache: LRUCache[tuple[str, int, float], ImageTk.PhotoImage] = LRUCache(cache_size)
        self.page_dimensions: LRUCache[tuple[str, int], tuple[float, float]] = LRUCache(cache_size)

    def clear(self) -> None:
        self.cache.clear()
        self.page_dimensions.clear()

    def clear_for_source(self, source_path: str) -> None:
        self.cache.remove_matching_prefix(source_path)
        self.page_dimensions.remove_matching_prefix(source_path)

    def get_page_dimensions(self, source_path: str, page_index: int) -> tuple[float, float]:
        telemetry = get_telemetry()
        key = (source_path, page_index)
        cached = self.page_dimensions.get(key)
        if cached is not None:
            telemetry.increment("preview_dimensions_cache_hit")
            logger.debug("Preview dimensions cache hit for %s page=%s", source_path, page_index)
            return cached

        telemetry.increment("preview_dimensions_cache_miss")
        logger.debug("Preview dimensions cache miss for %s page=%s", source_path, page_index)
        with telemetry.time_block("preview_dimensions_lookup_miss"):
            dimensions = get_page_box_dimensions(source_path, page_index)
        self.page_dimensions.put(key, dimensions)
        return dimensions

    def render(self, source_path: str, page_index: int, zoom: float) -> ImageTk.PhotoImage:
        telemetry = get_telemetry()
        key = (source_path, page_index, zoom)
        cached = self.cache.get(key)
        if cached is not None:
            telemetry.increment("preview_cache_hit")
            logger.debug("Preview cache hit for %s page=%s zoom=%.2f", source_path, page_index, zoom)
            return cached

        telemetry.increment("preview_cache_miss")
        logger.debug("Preview cache miss for %s page=%s zoom=%.2f", source_path, page_index, zoom)
        with telemetry.time_block("preview_render_miss"):
            image = render_page(source_path, page_index, zoom=zoom)
            photo = ImageTk.PhotoImage(image)
        self.cache.put(key, photo)
        return photo
