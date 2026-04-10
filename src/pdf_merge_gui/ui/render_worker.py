from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event, Thread
from typing import Any

from PIL import Image

from ..preview import DocumentSessionCache, render_page


@dataclass(frozen=True)
class RenderRequest:
    generation_id: int
    request_id: int
    source_path: str
    page_index: int
    rotation_degrees: int
    zoom: float


@dataclass(frozen=True)
class RenderResult:
    generation_id: int
    request_id: int
    width: int
    height: int
    mode: str
    pixels: bytes
    error: str | None = None


class RenderWorker:
    """Background renderer that owns document cache lifecycle."""

    def __init__(self, document_cache_size: int = 16) -> None:
        self._requests: Queue[RenderRequest | None] = Queue()
        self._results: Queue[RenderResult] = Queue()
        self._stop = Event()
        self._thread = Thread(
            target=self._run,
            kwargs={"document_cache_size": document_cache_size},
            daemon=True,
            name="pdf-render-worker",
        )
        self._thread.start()

    def submit(self, request: RenderRequest) -> None:
        self._requests.put(request)

    def poll_results(self, max_items: int = 32) -> list[RenderResult]:
        items: list[RenderResult] = []
        for _ in range(max_items):
            try:
                items.append(self._results.get_nowait())
            except Empty:
                break
        return items

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self._requests.put(None)
        self._thread.join(timeout=2.0)

    def _run(self, document_cache_size: int) -> None:
        document_cache = DocumentSessionCache(capacity=document_cache_size)
        try:
            while not self._stop.is_set():
                request = self._requests.get()
                if request is None:
                    break
                self._results.put(self._render(request, document_cache))
        finally:
            document_cache.clear()

    def _render(self, request: RenderRequest, document_cache: DocumentSessionCache) -> RenderResult:
        try:
            image = render_page(
                request.source_path,
                request.page_index,
                zoom=request.zoom,
                rotation_degrees=request.rotation_degrees,
                document_cache=document_cache,
            )
            rgb_image = image.convert("RGB") if image.mode != "RGB" else image
            return RenderResult(
                generation_id=request.generation_id,
                request_id=request.request_id,
                width=rgb_image.width,
                height=rgb_image.height,
                mode="RGB",
                pixels=rgb_image.tobytes(),
            )
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            return RenderResult(
                generation_id=request.generation_id,
                request_id=request.request_id,
                width=0,
                height=0,
                mode="RGB",
                pixels=b"",
                error=str(exc),
            )


def build_photo_image(result: RenderResult, image_tk: Any) -> Any:
    image = Image.frombytes(result.mode, (result.width, result.height), result.pixels)
    return image_tk.PhotoImage(image)
