from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any

from PIL import Image


class PreviewDependencyUnavailable(RuntimeError):
    """Raised when image preview dependencies are unavailable."""


class PreviewRenderError(RuntimeError):
    """Raised when a PDF page cannot be rendered as an image."""


class DocumentSessionCache:
    """LRU cache for open PDF document handles keyed by source path."""

    def __init__(self, capacity: int = 16) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = capacity
        self._cache: OrderedDict[str, Any] = OrderedDict()

    def get_or_open(self, source_path: str, fitz_module: Any) -> Any:
        path = Path(source_path)
        if not path.exists():
            self.clear_for_source(source_path)
            raise FileNotFoundError(source_path)

        cached = self._cache.pop(source_path, None)
        if cached is not None and self._is_valid_document(cached):
            self._cache[source_path] = cached
            return cached

        self._close_document(cached)
        opened = fitz_module.open(str(path))
        self._cache[source_path] = opened
        self._evict_if_necessary()
        return opened

    def clear_for_source(self, source_path: str) -> None:
        self._close_document(self._cache.pop(source_path, None))

    def clear(self) -> None:
        while self._cache:
            _, document = self._cache.popitem(last=False)
            self._close_document(document)

    def _evict_if_necessary(self) -> None:
        while len(self._cache) > self.capacity:
            _, document = self._cache.popitem(last=False)
            self._close_document(document)

    def _is_valid_document(self, document: Any) -> bool:
        if document is None:
            return False
        try:
            if bool(getattr(document, "is_closed", False)):
                return False
            _ = len(document)
            return True
        except Exception:
            return False

    def _close_document(self, document: Any) -> None:
        if document is None:
            return
        try:
            document.close()
        except Exception:
            pass


def _render_from_document(fitz_module: Any, document: Any, page_index: int, zoom: float) -> Image.Image:
    if page_index < 0 or page_index >= len(document):
        raise PreviewRenderError(f"Page index out of range: {page_index}")

    page = document.load_page(page_index)
    matrix = fitz_module.Matrix(zoom, zoom)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    return Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)


def render_page(
    pdf_path: str,
    page_index: int,
    zoom: float = 1.5,
    document_cache: DocumentSessionCache | None = None,
) -> Image.Image:
    """Render one PDF page to a PIL image using PyMuPDF.

    Args:
        pdf_path: Path to source PDF.
        page_index: Zero-based page index.
        zoom: Render zoom factor.

    Returns:
        PIL image for the selected page.
    """

    try:
        import fitz
    except ImportError as exc:
        raise PreviewDependencyUnavailable(
            "Image preview backend unavailable. Install 'pymupdf' for page rendering."
        ) from exc

    path = Path(pdf_path)
    if document_cache is None:
        try:
            with fitz.open(str(path)) as document:
                return _render_from_document(fitz, document, page_index, zoom)
        except PreviewRenderError:
            raise
        except Exception as exc:
            raise PreviewRenderError(str(exc)) from exc

    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            document = document_cache.get_or_open(str(path), fitz)
            return _render_from_document(fitz, document, page_index, zoom)
        except PreviewRenderError:
            raise
        except Exception as exc:
            last_error = exc
            document_cache.clear_for_source(str(path))
            if isinstance(exc, FileNotFoundError):
                break

    raise PreviewRenderError(str(last_error) if last_error is not None else "Unknown preview render error")
