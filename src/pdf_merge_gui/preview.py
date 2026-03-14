from __future__ import annotations

from pathlib import Path
from typing import Literal

from PIL import Image


class PreviewDependencyUnavailable(RuntimeError):
    """Raised when image preview dependencies are unavailable."""


class PreviewRenderError(RuntimeError):
    """Raised when a PDF page cannot be rendered as an image."""


QualityTier = Literal["draft", "focus"]


def render_page(
    pdf_path: str,
    page_index: int,
    zoom: float = 1.5,
    quality_tier: QualityTier = "focus",
) -> Image.Image:
    """Render one PDF page to a PIL image using PyMuPDF.

    Args:
        pdf_path: Path to source PDF.
        page_index: Zero-based page index.
        zoom: Render zoom factor.
        quality_tier: Render quality policy. Use "draft" for faster, lower-fidelity
            previews and "focus" for full-quality rendering.

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
            if quality_tier == "draft":
                effective_zoom = max(zoom * 0.6, 0.1)
                max_dimension = 1400
            else:
                effective_zoom = zoom
                max_dimension = None

            matrix = fitz.Matrix(effective_zoom, effective_zoom)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)

            if max_dimension is not None and max(image.width, image.height) > max_dimension:
                scale = max_dimension / max(image.width, image.height)
                target_width = max(1, int(image.width * scale))
                target_height = max(1, int(image.height * scale))
                image = image.resize((target_width, target_height), Image.Resampling.BILINEAR)

            return image
    except PreviewRenderError:
        raise
    except Exception as exc:
        raise PreviewRenderError(str(exc)) from exc
