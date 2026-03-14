from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import logging
from threading import Lock
from pathlib import Path
from typing import Callable

from PIL import Image

from ..preview import render_page
from ..utils.cache import LRUCache
from .telemetry import get_telemetry

RenderCallback = Callable[[tuple[str, int, float], Image.Image | None, Exception | None], None]
LOGGER = logging.getLogger(__name__)


class PreviewService:
    def __init__(self, cache_size: int = 100, *, worker_count: int = 1) -> None:
        self.cache: LRUCache[tuple[str, int, float], Image.Image] = LRUCache(cache_size)
        self._cache_lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=max(worker_count, 1), thread_name_prefix="preview-render")

    def clear(self) -> None:
        with self._cache_lock:
            self.cache.clear()

    def clear_for_source(self, source_path: str) -> None:
        with self._cache_lock:
            self.cache.remove_matching_prefix(source_path)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def render(self, source_path: str, page_index: int, zoom: float) -> Image.Image:
        telemetry = get_telemetry()
        key = (source_path, page_index, zoom)
        source_name = Path(source_path).name
        with self._cache_lock:
            cached = self.cache.get(key)
        if cached is not None:
            LOGGER.debug(
                "preview cache hit source=%s page_index=%s zoom=%.2f size=%sx%s mode=%s",
                source_name,
                page_index,
                zoom,
                cached.width,
                cached.height,
                cached.mode,
            )
            telemetry.increment("preview_cache_hit")
            return cached

        telemetry.increment("preview_cache_miss")
        with telemetry.time_block("preview_render_miss"):
            LOGGER.debug("preview render start source=%s page_index=%s zoom=%.2f", source_name, page_index, zoom)
            image = render_page(source_path, page_index, zoom=zoom)
            LOGGER.debug(
                "preview render done source=%s page_index=%s zoom=%.2f size=%sx%s mode=%s",
                source_name,
                page_index,
                zoom,
                image.width,
                image.height,
                image.mode,
            )

        with self._cache_lock:
            self.cache.put(key, image)
        return image

    def render_async(
        self,
        source_path: str,
        page_index: int,
        zoom: float,
        callback: RenderCallback,
    ) -> Future[None]:
        key = (source_path, page_index, zoom)

        def worker() -> None:
            try:
                image = self.render(source_path, page_index, zoom)
            except Exception as exc:  # pragma: no cover - exercised via callback handling.
                LOGGER.exception(
                    "preview async render failed source=%s page_index=%s zoom=%.2f",
                    Path(source_path).name,
                    page_index,
                    zoom,
                )
                callback(key, None, exc)
                return
            callback(key, image, None)

        return self._executor.submit(worker)
