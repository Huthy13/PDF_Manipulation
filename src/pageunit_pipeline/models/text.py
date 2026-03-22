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
