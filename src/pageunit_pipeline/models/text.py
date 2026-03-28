"""Text-related models.

Canonical coordinate convention:
- Origin is top-left of the page.
- x increases to the right, y increases downward.
"""

from enum import Enum

from pydantic import BaseModel, Field

from .bbox import BBox


class BlockType(str, Enum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TABLE_TEXT = "table_text"
    FOOTER = "footer"
    HEADER = "header"
    UNKNOWN = "unknown"


class ContentRole(str, Enum):
    """Lightweight semantic role for normalized page content blocks."""

    QUOTE_HEADER = "quote_header"
    SCOPE_NARRATIVE = "scope_narrative"
    NOTE = "note"
    LABOR = "labor"
    EQUIPMENT_DETAIL = "equipment_detail"
    PRICING_TERMS = "pricing_terms"
    TABLE_ROW = "table_row"
    GENERIC = "generic"


class QualityFlag(str, Enum):
    """Known uncertainty/quality signals for page/content normalization."""

    FIELD_INFERRED_FROM_LAYOUT = "field_inferred_from_layout"
    TABLE_STRUCTURE_IMPLICIT = "table_structure_implicit"
    POSSIBLE_SCOPE_NOTE = "possible_scope_note"
    POSSIBLE_LINE_ITEM_DETAIL = "possible_line_item_detail"
    LOW_CONFIDENCE_ROLE_ASSIGNMENT = "low_confidence_role_assignment"


class LineUnit(BaseModel):
    """Single extracted text line with optional spatial metadata."""

    text: str = Field(default="")
    bbox: BBox | None = Field(default=None)
    confidence: float | None = Field(default=None)
    language: str = Field(default="")


class TextBlock(BaseModel):
    """Grouped text content represented as one logical block."""

    block_type: BlockType = Field(default=BlockType.UNKNOWN)
    text: str = Field(default="")
    lines: list[LineUnit] = Field(default_factory=list)
    bbox: BBox | None = Field(default=None)
    reading_order: int | None = Field(default=None)


class PageContentBlock(BaseModel):
    """Geometry-light semantic block consumed by downstream chunkers."""

    role: ContentRole = Field(default=ContentRole.GENERIC)
    text: str = Field(default="")
    source_block_type: BlockType = Field(default=BlockType.UNKNOWN)
    reading_order: int | None = Field(default=None)
    promoted_fields: dict[str, str] = Field(default_factory=dict)
    quality_flags: list[QualityFlag] = Field(default_factory=list)
