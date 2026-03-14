from __future__ import annotations

from typing import Literal

from PIL import ImageTk

from ..preview import render_page
from ..utils.cache import EvictionReason, LRUCache
from .telemetry import get_telemetry

PreviewMode = Literal["single", "final"]


class PreviewService:
    ZOOM_BUCKET_STEP = 0.1
    DEFAULT_MAX_PIXEL_COST = 240_000_000

    def __init__(self, cache_size: int = 100, max_pixel_cost: int = DEFAULT_MAX_PIXEL_COST) -> None:
        self.cache: LRUCache[tuple[str, int, float, str], ImageTk.PhotoImage] = LRUCache(
            capacity=cache_size,
            max_cost=max_pixel_cost,
            cost_fn=self._estimate_pixel_cost,
            on_evict=self._on_cache_evict,
        )

    @classmethod
    def quantize_zoom(cls, zoom: float) -> float:
        bucketed = round(zoom / cls.ZOOM_BUCKET_STEP) * cls.ZOOM_BUCKET_STEP
        return round(bucketed, 2)

    @staticmethod
    def _dimension_to_int(value: object) -> int:
        if callable(value):
            value = value()
        if isinstance(value, (int, float)):
            return int(value)
        raise TypeError(f"unsupported preview image dimension type: {type(value).__name__}")

    @classmethod
    def _estimate_pixel_cost(cls, image: ImageTk.PhotoImage) -> int:
        width = cls._dimension_to_int(getattr(image, "width"))
        height = cls._dimension_to_int(getattr(image, "height"))
        return max(1, width * height * 4)

    def _on_cache_evict(
        self,
        key: tuple[str, int, float, str],
        _value: ImageTk.PhotoImage,
        _cost: int,
        reason: EvictionReason,
    ) -> None:
        telemetry = get_telemetry()
        if reason in {"capacity", "memory"}:
            telemetry.increment("preview_cache_eviction", tags={"reason": reason})

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
        mode: PreviewMode = "single",
    ) -> ImageTk.PhotoImage:
        telemetry = get_telemetry()
        zoom_bucket = self.quantize_zoom(zoom)
        tags = {"mode": mode, "zoom_bucket": f"{zoom_bucket:.2f}"}
        key = (source_path, page_index, zoom_bucket, quality_tier)
        cached = self.cache.get(key)
        if cached is not None:
            telemetry.increment("preview_cache_hit", tags=tags)
            return cached

        telemetry.increment("preview_cache_miss", tags=tags)
        with telemetry.time_block("preview_render_miss", tags=tags):
            image = render_page(source_path, page_index, zoom=zoom_bucket, quality_tier=quality_tier)
            photo = ImageTk.PhotoImage(image)
        self.cache.put(key, photo)
        return photo
