from __future__ import annotations

from pathlib import Path

from PIL import Image


def _open_fitz_document(pdf_path: str):
    try:
        import fitz
    except ImportError as exc:
        raise PreviewDependencyUnavailable(
            "Image preview backend unavailable. Install 'pymupdf' for page rendering."
        ) from exc
    path = Path(pdf_path)
    return fitz, fitz.open(str(path))


class PreviewDependencyUnavailable(RuntimeError):
    """Raised when image preview dependencies are unavailable."""


class PreviewRenderError(RuntimeError):
    """Raised when a PDF page cannot be rendered as an image."""


def render_page(pdf_path: str, page_index: int, zoom: float = 1.5) -> Image.Image:
    """Render one PDF page to a PIL image using PyMuPDF.

    Args:
        pdf_path: Path to source PDF.
        page_index: Zero-based page index.
        zoom: Render zoom factor.

    Returns:
        PIL image for the selected page.
    """

    try:
        fitz, doc = _open_fitz_document(pdf_path)
        with doc:
            if page_index < 0 or page_index >= len(doc):
                raise PreviewRenderError(f"Page index out of range: {page_index}")
            page = doc.load_page(page_index)
            matrix = fitz.Matrix(zoom, zoom)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            return Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
    except PreviewRenderError:
        raise
    except Exception as exc:
        raise PreviewRenderError(str(exc)) from exc


def read_page_dimensions(pdf_path: str, page_index: int) -> tuple[float, float]:
    """Read intrinsic page dimensions from PDF metadata without rasterization."""

    try:
        _fitz, doc = _open_fitz_document(pdf_path)
        with doc:
            if page_index < 0 or page_index >= len(doc):
                raise PreviewRenderError(f"Page index out of range: {page_index}")
            page = doc.load_page(page_index)
            rect = page.rect
            return max(float(rect.width), 1.0), max(float(rect.height), 1.0)
    except PreviewRenderError:
        raise
    except Exception as exc:
        raise PreviewRenderError(str(exc)) from exc
