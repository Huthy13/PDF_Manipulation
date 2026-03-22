"""Raw adapter payload types for page extraction pipeline.

These dataclasses intentionally model low-level provider output before
normalization into canonical page-unit models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from pageunit_pipeline.models.bbox import BBox

ProviderMetadata = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PageDimensions:
    """Physical page dimensions for a single page."""

    width: float
    height: float
    unit: str = "pt"


@dataclass(frozen=True, slots=True)
class RawLineCandidate:
    """Line-level text candidate from an extraction provider."""

    text: str
    bbox: BBox | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class RawBlockCandidate:
    """Block-level text candidate with optional nested line candidates."""

    text: str
    bbox: BBox | None = None
    lines: tuple[RawLineCandidate, ...] = field(default_factory=tuple)
    reading_order: int | None = None


@dataclass(frozen=True, slots=True)
class EmbeddedImageReference:
    """Reference to an embedded/raster image associated with a page."""

    image_id: str
    bbox: BBox | None = None
    mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class RawTableCandidate:
    """Provider table candidate with optional geometry."""

    table_id: str
    bbox: BBox | None = None
    content: ProviderMetadata = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RawPageExtraction:
    """Intermediate raw page payload returned by extraction adapters."""

    page_number: int
    page_dimensions: PageDimensions
    full_text_candidate: str
    block_candidates: tuple[RawBlockCandidate, ...] = field(default_factory=tuple)
    line_candidates: tuple[RawLineCandidate, ...] = field(default_factory=tuple)
    embedded_image_references: tuple[EmbeddedImageReference, ...] = field(
        default_factory=tuple
    )
    provider_metadata: ProviderMetadata = field(default_factory=dict)
    provider_name: str = ""
    provider_version: str = ""


@dataclass(frozen=True, slots=True)
class RawTableExtraction:
    """Intermediate table-only payload returned by table adapters."""

    page_number: int
    page_dimensions: PageDimensions
    tables: tuple[RawTableCandidate, ...] = field(default_factory=tuple)
    provider_metadata: ProviderMetadata = field(default_factory=dict)
    provider_name: str = ""
    provider_version: str = ""


@dataclass(frozen=True, slots=True)
class RawImageDetection:
    """Intermediate image-detection payload returned by image adapters."""

    page_number: int
    page_dimensions: PageDimensions
    embedded_image_references: tuple[EmbeddedImageReference, ...] = field(
        default_factory=tuple
    )
    provider_metadata: ProviderMetadata = field(default_factory=dict)
    provider_name: str = ""
    provider_version: str = ""
