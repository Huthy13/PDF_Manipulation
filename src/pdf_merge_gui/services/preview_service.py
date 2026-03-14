from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageTk

from ..preview import render_page
from .telemetry import get_telemetry

PrimaryCacheKey = tuple[str, int, float]
UiCacheKey = tuple[int, tuple[float]]


@dataclass
class _PrimaryEntry:
    image: Image.Image
    estimated_bytes: int


@dataclass
class _UiEntry:
    image: ImageTk.PhotoImage
    estimated_bytes: int
    decoded_image_id: int


class PreviewService:
    def __init__(
        self,
        cache_size: int = 100,
        max_cache_bytes: int = 256 * 1024 * 1024,
        offscreen_cache_size: int = 32,
        offscreen_cache_bytes: int = 64 * 1024 * 1024,
        ui_cache_size: int = 48,
        ui_cache_bytes: int = 96 * 1024 * 1024,
        ui_offscreen_cache_size: int = 16,
        ui_offscreen_cache_bytes: int = 24 * 1024 * 1024,
    ) -> None:
        self.max_cache_items = max(cache_size, 1)
        self.max_cache_bytes = max(max_cache_bytes, 1)
        self.max_offscreen_items = max(offscreen_cache_size, 0)
        self.max_offscreen_bytes = max(offscreen_cache_bytes, 0)

        self.max_ui_items = max(ui_cache_size, 1)
        self.max_ui_bytes = max(ui_cache_bytes, 1)
        self.max_ui_offscreen_items = max(ui_offscreen_cache_size, 0)
        self.max_ui_offscreen_bytes = max(ui_offscreen_cache_bytes, 0)

        self._decoded_cache: OrderedDict[PrimaryCacheKey, _PrimaryEntry] = OrderedDict()
        self._decoded_cache_bytes = 0
        self._ui_cache: OrderedDict[UiCacheKey, _UiEntry] = OrderedDict()
        self._ui_cache_bytes = 0
        self._decoded_ids_by_source_page: dict[tuple[str, int, float], int] = {}

    def clear(self) -> None:
        self._decoded_cache.clear()
        self._decoded_cache_bytes = 0
        self._ui_cache.clear()
        self._ui_cache_bytes = 0
        self._decoded_ids_by_source_page.clear()

    def clear_for_source(self, source_path: str) -> None:
        doomed_decoded = [key for key in self._decoded_cache if key[0] == source_path]
        doomed_decoded_ids: set[int] = set()
        for key in doomed_decoded:
            entry = self._decoded_cache.pop(key)
            self._decoded_cache_bytes = max(0, self._decoded_cache_bytes - entry.estimated_bytes)
            doomed_decoded_ids.add(id(entry.image))

        for key in list(self._decoded_ids_by_source_page):
            if key[0] == source_path:
                doomed_decoded_ids.add(self._decoded_ids_by_source_page[key])
                self._decoded_ids_by_source_page.pop(key, None)

        for key in list(self._ui_cache):
            if self._ui_cache[key].decoded_image_id in doomed_decoded_ids:
                self._remove_ui_key(key)

    def zoom_bucket(self, zoom: float) -> float:
        return round(zoom, 2)

    def decoded_cache_key(self, source_path: str, page_index: int, zoom: float) -> PrimaryCacheKey:
        return (source_path, page_index, self.zoom_bucket(zoom))

    def get_decoded_image(self, source_path: str, page_index: int, zoom: float) -> tuple[PrimaryCacheKey, Image.Image]:
        telemetry = get_telemetry()
        key = self.decoded_cache_key(source_path, page_index, zoom)
        cached = self._decoded_cache_get(key)
        if cached is not None:
            telemetry.increment("preview_cache_hit")
            return key, cached.image

        telemetry.increment("preview_cache_miss")
        with telemetry.time_block("preview_render_miss"):
            image = render_page(source_path, page_index, zoom=zoom)
        self.store_decoded_image(key, image)
        return key, image

    def store_decoded_image(self, key: PrimaryCacheKey, image: Image.Image) -> None:
        self._decoded_cache_put(key, image)

    def get_ui_image(self, decoded_image: Image.Image, widget_scale_context: tuple[float]) -> ImageTk.PhotoImage:
        key: UiCacheKey = (id(decoded_image), widget_scale_context)
        cached = self._ui_cache_get(key)
        if cached is not None:
            return cached.image
        photo = ImageTk.PhotoImage(decoded_image)
        self._ui_cache_put(key, photo, decoded_image)
        return photo

    def trim_to_budget(
        self,
        *,
        visible_decoded_keys: set[PrimaryCacheKey],
        visible_ui_keys: set[UiCacheKey],
    ) -> None:
        self._trim_decoded(visible_decoded_keys)
        self._trim_ui(visible_ui_keys)

    def _decoded_cache_get(self, key: PrimaryCacheKey) -> Optional[_PrimaryEntry]:
        entry = self._decoded_cache.pop(key, None)
        if entry is None:
            return None
        self._decoded_cache[key] = entry
        return entry

    def _decoded_cache_put(self, key: PrimaryCacheKey, image: Image.Image) -> None:
        estimated_bytes = self._estimate_image_bytes(image)
        existing = self._decoded_cache.pop(key, None)
        if existing is not None:
            self._decoded_cache_bytes = max(0, self._decoded_cache_bytes - existing.estimated_bytes)
            prior_id = id(existing.image)
            if prior_id != id(image):
                self._remove_ui_for_decoded_id(prior_id)
        self._decoded_cache[key] = _PrimaryEntry(image=image, estimated_bytes=estimated_bytes)
        self._decoded_cache_bytes += estimated_bytes
        self._decoded_ids_by_source_page[key] = id(image)

    def _ui_cache_get(self, key: UiCacheKey) -> Optional[_UiEntry]:
        entry = self._ui_cache.pop(key, None)
        if entry is None:
            return None
        self._ui_cache[key] = entry
        return entry

    def _ui_cache_put(self, key: UiCacheKey, image: ImageTk.PhotoImage, decoded_image: Image.Image) -> None:
        estimated_bytes = self._estimate_photo_bytes(decoded_image)
        existing = self._ui_cache.pop(key, None)
        if existing is not None:
            self._ui_cache_bytes = max(0, self._ui_cache_bytes - existing.estimated_bytes)
        self._ui_cache[key] = _UiEntry(image=image, estimated_bytes=estimated_bytes, decoded_image_id=id(decoded_image))
        self._ui_cache_bytes += estimated_bytes

    def _trim_decoded(self, visible_keys: set[PrimaryCacheKey]) -> None:
        while len(self._decoded_cache) > self.max_cache_items or self._decoded_cache_bytes > self.max_cache_bytes:
            if not self._evict_decoded_lru(visible_keys):
                break
        offscreen = [key for key in self._decoded_cache if key not in visible_keys]
        offscreen_bytes = sum(self._decoded_cache[key].estimated_bytes for key in offscreen)
        while len(offscreen) > self.max_offscreen_items or offscreen_bytes > self.max_offscreen_bytes:
            key = offscreen.pop(0)
            offscreen_bytes -= self._decoded_cache[key].estimated_bytes
            self._remove_decoded_key(key)

    def _trim_ui(self, visible_keys: set[UiCacheKey]) -> None:
        while len(self._ui_cache) > self.max_ui_items or self._ui_cache_bytes > self.max_ui_bytes:
            if not self._evict_ui_lru(visible_keys):
                break
        offscreen = [key for key in self._ui_cache if key not in visible_keys]
        offscreen_bytes = sum(self._ui_cache[key].estimated_bytes for key in offscreen)
        while len(offscreen) > self.max_ui_offscreen_items or offscreen_bytes > self.max_ui_offscreen_bytes:
            key = offscreen.pop(0)
            offscreen_bytes -= self._ui_cache[key].estimated_bytes
            self._remove_ui_key(key)

    def _evict_decoded_lru(self, protected_keys: set[PrimaryCacheKey]) -> bool:
        for key in list(self._decoded_cache.keys()):
            if key in protected_keys:
                continue
            self._remove_decoded_key(key)
            return True
        return False

    def _evict_ui_lru(self, protected_keys: set[UiCacheKey]) -> bool:
        for key in list(self._ui_cache.keys()):
            if key in protected_keys:
                continue
            self._remove_ui_key(key)
            return True
        return False

    def _remove_decoded_key(self, key: PrimaryCacheKey) -> None:
        entry = self._decoded_cache.pop(key, None)
        if entry is None:
            return
        self._decoded_cache_bytes = max(0, self._decoded_cache_bytes - entry.estimated_bytes)
        self._decoded_ids_by_source_page.pop(key, None)
        self._remove_ui_for_decoded_id(id(entry.image))

    def _remove_ui_for_decoded_id(self, decoded_image_id: int) -> None:
        for key in list(self._ui_cache.keys()):
            if self._ui_cache[key].decoded_image_id == decoded_image_id:
                self._remove_ui_key(key)

    def _remove_ui_key(self, key: UiCacheKey) -> None:
        entry = self._ui_cache.pop(key, None)
        if entry is None:
            return
        self._ui_cache_bytes = max(0, self._ui_cache_bytes - entry.estimated_bytes)

    def _estimate_image_bytes(self, image: Image.Image) -> int:
        bands = len(image.getbands()) or 4
        return max(image.width * image.height * bands, 1)

    def _estimate_photo_bytes(self, source_image: Image.Image) -> int:
        return self._estimate_image_bytes(source_image)
