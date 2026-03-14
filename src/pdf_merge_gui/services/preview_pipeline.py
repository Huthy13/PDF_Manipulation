from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, PriorityQueue
from threading import Event, Lock, Thread
from typing import Callable, Optional

from PIL import Image

from ..preview import render_page


@dataclass(frozen=True)
class RenderRequest:
    source_path: str
    page_index: int
    zoom: float
    generation_id: int


@dataclass
class RenderResult:
    request: RenderRequest
    image: Optional[Image.Image] = None
    error: Optional[Exception] = None


class PreviewRenderPipeline:
    """Background page renderer for final-mode preview virtual windows."""

    def __init__(self, on_result: Callable[[RenderResult], None], workers: int = 1) -> None:
        self._on_result = on_result
        self._queue: PriorityQueue[tuple[int, int, RenderRequest]] = PriorityQueue()
        self._shutdown = Event()
        self._lock = Lock()
        self._counter = 0
        self._active_generation = 0
        self._threads = [
            Thread(target=self._worker_loop, name=f"preview-render-{index}", daemon=True)
            for index in range(max(workers, 1))
        ]
        for thread in self._threads:
            thread.start()

    def set_active_generation(self, generation_id: int) -> None:
        with self._lock:
            self._active_generation = generation_id

    def submit(self, request: RenderRequest, *, priority: int) -> None:
        with self._lock:
            self._counter += 1
            order = self._counter
        self._queue.put((priority, order, request))

    def stop(self) -> None:
        self._shutdown.set()
        for _ in self._threads:
            self._queue.put((10_000, 10_000, RenderRequest("", -1, 1.0, -1)))
        for thread in self._threads:
            thread.join(timeout=0.5)

    def _is_stale(self, generation_id: int) -> bool:
        with self._lock:
            return generation_id != self._active_generation

    def _worker_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                _, _, request = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                if request.generation_id < 0:
                    continue
                if self._is_stale(request.generation_id):
                    continue
                try:
                    image = render_page(request.source_path, request.page_index, zoom=request.zoom)
                    result = RenderResult(request=request, image=image)
                except Exception as exc:  # noqa: BLE001
                    result = RenderResult(request=request, error=exc)
                self._on_result(result)
            finally:
                self._queue.task_done()
