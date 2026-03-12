from __future__ import annotations

from PIL import ImageTk

from ..preview import render_page
from ..utils.cache import LRUCache


class PreviewService:
    def __init__(self, cache_size: int = 100) -> None:
        self.cache: LRUCache[tuple[str, int, float], ImageTk.PhotoImage] = LRUCache(cache_size)

    def clear(self) -> None:
        self.cache.clear()

    def clear_for_source(self, source_path: str) -> None:
        self.cache.remove_matching_prefix(source_path)

    def render(self, source_path: str, page_index: int, zoom: float) -> ImageTk.PhotoImage:
        key = (source_path, page_index, zoom)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        image = render_page(source_path, page_index, zoom=zoom)
        photo = ImageTk.PhotoImage(image)
        self.cache.put(key, photo)
        return photo
