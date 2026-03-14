from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from PIL import Image

from .utils.cache import LRUCache


class PreviewDependencyUnavailable(RuntimeError):
    """Raised when image preview dependencies are unavailable."""


class PreviewRenderError(RuntimeError):
    """Raised when a PDF page cannot be rendered as an image."""


@dataclass(frozen=True)
class SourceFingerprint:
    path: str
    mtime_ns: int
    size: int


def build_source_fingerprint(pdf_path: str) -> SourceFingerprint:
    resolved = Path(pdf_path).resolve()
    stat = resolved.stat()
    return SourceFingerprint(path=str(resolved), mtime_ns=stat.st_mtime_ns, size=stat.st_size)


class DocumentHandleCache:
    """Reuse open PDF handles keyed by source fingerprint."""

    def __init__(self, max_open_documents: int = 12) -> None:
        self._documents: LRUCache[SourceFingerprint, object] = LRUCache(max_open_documents)
        self._path_index: dict[str, SourceFingerprint] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            for document in self._documents.values():
                self._close_document(document)
            self._documents.clear()
            self._path_index.clear()

    def remove_for_path(self, source_path: str) -> None:
        normalized = str(Path(source_path).resolve())
        with self._lock:
            fingerprint = self._path_index.pop(normalized, None)
            if fingerprint is None:
                return
            stale = self._documents.pop(fingerprint)
            self._close_document(stale)

    def get_document(self, source_path: str) -> tuple[SourceFingerprint, object]:
        fitz = _import_fitz()
        fingerprint = build_source_fingerprint(source_path)
        with self._lock:
            previous = self._path_index.get(fingerprint.path)
            if previous is not None and previous != fingerprint:
                stale = self._documents.pop(previous)
                self._close_document(stale)

            cached = self._documents.get(fingerprint)
            if cached is not None:
                self._path_index[fingerprint.path] = fingerprint
                return fingerprint, cached

            document = fitz.open(fingerprint.path)
            self._documents.put(fingerprint, document)
            self._path_index[fingerprint.path] = fingerprint
            self._trim_closed_documents()
            return fingerprint, document

    def _trim_closed_documents(self) -> None:
        known = set(self._documents.keys())
        doomed_paths = [path for path, fp in self._path_index.items() if fp not in known]
        for path in doomed_paths:
            self._path_index.pop(path, None)

    @staticmethod
    def _close_document(document: object | None) -> None:
        if document is None:
            return
        close = getattr(document, "close", None)
        if callable(close):
            close()


_document_handle_cache = DocumentHandleCache()


def render_page(
    pdf_path: str,
    page_index: int,
    zoom: float = 1.5,
    *,
    document_cache: DocumentHandleCache | None = None,
) -> tuple[SourceFingerprint, Image.Image]:
    """Render one PDF page to a PIL image using PyMuPDF."""

    cache = document_cache or _document_handle_cache

    try:
        fingerprint, document = cache.get_document(pdf_path)
        if page_index < 0 or page_index >= len(document):
            raise PreviewRenderError(f"Page index out of range: {page_index}")
        fitz = _import_fitz()
        page = document.load_page(page_index)
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
        return fingerprint, image
    except PreviewRenderError:
        raise
    except Exception as exc:
        raise PreviewRenderError(str(exc)) from exc


def clear_document_handle_cache() -> None:
    _document_handle_cache.clear()


def clear_document_handle_for_path(source_path: str) -> None:
    _document_handle_cache.remove_for_path(source_path)


def _import_fitz():
    try:
        import fitz
    except ImportError as exc:
        raise PreviewDependencyUnavailable(
            "Image preview backend unavailable. Install 'pymupdf' for page rendering."
        ) from exc
    return fitz
