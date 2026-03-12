from __future__ import annotations

from pathlib import Path

from PIL import Image


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
        import fitz
    except ImportError as exc:
        raise PreviewDependencyUnavailable(
            "Image preview backend unavailable. Install 'pymupdf' for page rendering."
        ) from exc

    path = Path(pdf_path)
    try:
        with fitz.open(str(path)) as doc:
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
