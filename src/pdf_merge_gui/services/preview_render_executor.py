from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Protocol

from PIL import Image

class PreviewRenderable(Protocol):
    def render_pil(self, source_path: str, page_index: int, zoom: float) -> Image.Image:
        ...


@dataclass(frozen=True)
class PreviewRenderJob:
    token: int
    page_slot: int
    source_path: str
    page_index: int
    zoom: float


@dataclass(frozen=True)
class PreviewRenderResult:
    token: int
    page_slot: int
    source_path: str
    page_index: int
    zoom: float
    image: Image.Image


class PreviewRenderExecutor:
    def __init__(self, preview_service: PreviewRenderable, max_workers: Optional[int] = None) -> None:
        self._preview_service = preview_service
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="preview-render")

    def submit(self, job: PreviewRenderJob) -> Future[PreviewRenderResult]:
        return self._executor.submit(self._run_job, job)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run_job(self, job: PreviewRenderJob) -> PreviewRenderResult:
        image = self._preview_service.render_pil(job.source_path, job.page_index, job.zoom)
        return PreviewRenderResult(
            token=job.token,
            page_slot=job.page_slot,
            source_path=job.source_path,
            page_index=job.page_index,
            zoom=job.zoom,
            image=image,
        )
