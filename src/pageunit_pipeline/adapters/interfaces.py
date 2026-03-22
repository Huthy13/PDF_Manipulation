"""Narrow adapter interfaces for deterministic page extraction stages."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import RawImageDetection, RawPageExtraction, RawTableExtraction


@runtime_checkable
class PdfParserAdapter(Protocol):
    """Extract raw text/layout signals directly from a PDF page.

    Implementations should be deterministic and side-effect free for identical
    inputs. They must not depend on process-global mutable state.
    """

    @property
    def provider_name(self) -> str:
        """Provider identifier (e.g., library/service name)."""

    @property
    def provider_version(self) -> str:
        """Provider semantic version or build identifier."""

    def extract_page(self, *, pdf_bytes: bytes, page_number: int) -> RawPageExtraction:
        """Return raw extraction payload for one page (1-indexed)."""


@runtime_checkable
class OcrAdapter(Protocol):
    """Extract OCR candidates from a rasterized page image.

    Implementations should be deterministic and side-effect free for identical
    inputs. They must not depend on process-global mutable state.
    """

    @property
    def provider_name(self) -> str:
        """Provider identifier (e.g., OCR engine name)."""

    @property
    def provider_version(self) -> str:
        """Provider semantic version or build identifier."""

    def extract_page(self, *, image_bytes: bytes, page_number: int) -> RawPageExtraction:
        """Return OCR-driven raw extraction payload for one page (1-indexed)."""


@runtime_checkable
class TableExtractorAdapter(Protocol):
    """Extract raw table candidates for a page.

    Implementations should be deterministic and side-effect free for identical
    inputs. They must not depend on process-global mutable state.
    """

    @property
    def provider_name(self) -> str:
        """Provider identifier (e.g., table model name)."""

    @property
    def provider_version(self) -> str:
        """Provider semantic version or build identifier."""

    def extract_tables(self, *, page: RawPageExtraction) -> RawTableExtraction:
        """Return table-only extraction payload for a page."""


@runtime_checkable
class ImageDetectorAdapter(Protocol):
    """Detect image regions/refs associated with a page.

    Implementations should be deterministic and side-effect free for identical
    inputs. They must not depend on process-global mutable state.
    """

    @property
    def provider_name(self) -> str:
        """Provider identifier (e.g., detector model name)."""

    @property
    def provider_version(self) -> str:
        """Provider semantic version or build identifier."""

    def detect_images(self, *, page: RawPageExtraction) -> RawImageDetection:
        """Return image-detection payload for a page."""
