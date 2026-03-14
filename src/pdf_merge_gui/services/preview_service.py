from __future__ import annotations

from pathlib import Path

from dataclasses import dataclass
from io import BytesIO
from threading import RLock

from PIL import Image, ImageTk

from ..preview import (
    SourceFingerprint,
    build_source_fingerprint,
    clear_document_handle_cache,
    clear_document_handle_for_path,
    render_page,
)
from ..utils.cache import LRUCache
from .telemetry import get_telemetry


@dataclass(frozen=True)
class RasterCacheKey:
    source_fingerprint: SourceFingerprint
    page_index: int
    zoom_bucket: int
    render_profile: str


class PreviewService:
    def __init__(
        self,
        cache_size: int = 100,
        photo_cache_size: int = 24,
        render_profile: str = "rgb",
        zoom_bucket_step_percent: int = 5,
        interaction_zoom_bucket_step_percent: int = 10,
        preview_max_dpi: int = 216,
    ) -> None:
        self.raster_cache: LRUCache[RasterCacheKey, bytes] = LRUCache(cache_size)
        self.photo_cache: LRUCache[RasterCacheKey, ImageTk.PhotoImage] = LRUCache(photo_cache_size)
        self.render_profile = render_profile
        self.zoom_bucket_step_percent = max(1, zoom_bucket_step_percent)
        self.interaction_zoom_bucket_step_percent = max(1, interaction_zoom_bucket_step_percent)
        self.preview_max_dpi = max(72, preview_max_dpi)
        self._cache_lock = RLock()

    def clear(self) -> None:
        with self._cache_lock:
            self.raster_cache.clear()
            self.photo_cache.clear()
        clear_document_handle_cache()

    def clear_for_source(self, source_path: str) -> None:
        normalized_source = str(Path(source_path).resolve())
        clear_document_handle_for_path(normalized_source)

        with self._cache_lock:
            doomed = [
                key
                for key in self.raster_cache.keys()
                if isinstance(key, RasterCacheKey) and key.source_fingerprint.path == normalized_source
            ]
            for key in doomed:
                self.raster_cache.pop(key)
                self.photo_cache.pop(key)

    def render_pil(
        self,
        source_path: str,
        page_index: int,
        zoom: float,
        bucket_step_percent: int | None = None,
    ) -> Image.Image:
        telemetry = get_telemetry()
        effective_zoom = self._effective_preview_zoom(zoom)
        zoom_bucket = self._zoom_bucket(effective_zoom, bucket_step_percent=bucket_step_percent)
        image_zoom = zoom_bucket / 100.0

        fingerprint = build_source_fingerprint(source_path)
        key = RasterCacheKey(
            source_fingerprint=fingerprint,
            page_index=page_index,
            zoom_bucket=zoom_bucket,
            render_profile=self.render_profile,
        )

        with self._cache_lock:
            cached_bytes = self.raster_cache.get(key)
        if cached_bytes is not None:
            telemetry.increment("preview_cache_hit")
            return self._decode_image(cached_bytes)

        telemetry.increment("preview_cache_miss")
        with telemetry.time_block("preview_render_miss"):
            _fingerprint_from_render, rendered = render_page(source_path, page_index, zoom=image_zoom)
            encoded = self._encode_image(rendered)
        with self._cache_lock:
            self.raster_cache.put(key, encoded)
        return rendered

    def render(
        self,
        source_path: str,
        page_index: int,
        zoom: float,
        bucket_step_percent: int | None = None,
    ) -> ImageTk.PhotoImage:
        effective_zoom = self._effective_preview_zoom(zoom)
        zoom_bucket = self._zoom_bucket(effective_zoom, bucket_step_percent=bucket_step_percent)
        fingerprint = build_source_fingerprint(source_path)
        key = RasterCacheKey(
            source_fingerprint=fingerprint,
            page_index=page_index,
            zoom_bucket=zoom_bucket,
            render_profile=self.render_profile,
        )

        telemetry = get_telemetry()
        with self._cache_lock:
            cached_photo = self.photo_cache.get(key)
        if cached_photo is not None:
            telemetry.increment("preview_cache_hit")
            return cached_photo

        image = self.render_pil(source_path, page_index, effective_zoom, bucket_step_percent=bucket_step_percent)
        photo = ImageTk.PhotoImage(image)
        with self._cache_lock:
            self.photo_cache.put(key, photo)
        return photo

    def nearest_cached_photo(
        self,
        source_path: str,
        page_index: int,
        zoom: float,
        bucket_step_percent: int | None = None,
    ) -> tuple[float, ImageTk.PhotoImage] | None:
        """Return nearest cached zoom image if available without triggering raster render."""
        effective_zoom = self._effective_preview_zoom(zoom)
        target_bucket = self._zoom_bucket(effective_zoom, bucket_step_percent=bucket_step_percent)
        fingerprint = build_source_fingerprint(source_path)
        nearest_key = self._nearest_cached_key(fingerprint, page_index, target_bucket)
        if nearest_key is None:
            return None

        telemetry = get_telemetry()
        with self._cache_lock:
            cached_photo = self.photo_cache.get(nearest_key)
            if cached_photo is not None:
                telemetry.increment("preview_cache_hit")
                return nearest_key.zoom_bucket / 100.0, cached_photo

            cached_bytes = self.raster_cache.get(nearest_key)
            if cached_bytes is None:
                return None

        telemetry.increment("preview_cache_hit")
        photo = ImageTk.PhotoImage(self._decode_image(cached_bytes))
        with self._cache_lock:
            self.photo_cache.put(nearest_key, photo)
        return nearest_key.zoom_bucket / 100.0, photo

    def _nearest_cached_key(
        self,
        fingerprint: SourceFingerprint,
        page_index: int,
        target_bucket: int,
    ) -> RasterCacheKey | None:
        with self._cache_lock:
            candidate_keys = [
                key
                for key in self.raster_cache.keys()
                if isinstance(key, RasterCacheKey)
                and key.source_fingerprint == fingerprint
                and key.page_index == page_index
                and key.render_profile == self.render_profile
            ]
        if not candidate_keys:
            return None
        return min(candidate_keys, key=lambda key: abs(key.zoom_bucket - target_bucket))

    def _effective_preview_zoom(self, zoom: float) -> float:
        max_zoom = self.preview_max_dpi / 72.0
        return min(max(zoom, 0.01), max_zoom)

    def _zoom_bucket(self, zoom: float, bucket_step_percent: int | None = None) -> int:
        step = max(1, bucket_step_percent or self.zoom_bucket_step_percent)
        raw_bucket = int(round(zoom * 100))
        quantized = int(round(raw_bucket / step) * step)
        return max(step, quantized)

    @staticmethod
    def _encode_image(image: Image.Image) -> bytes:
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    @staticmethod
    def _decode_image(encoded: bytes) -> Image.Image:
        with BytesIO(encoded) as buffer:
            image = Image.open(buffer)
            return image.copy()
