"""Page-level schema for unified extraction output.

Canonical coordinate convention:
- Origin is top-left of the page.
- x increases to the right, y increases downward.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .image import ImageRegion
from .table import TableUnit
from .text import TextBlock


class ExtractionMethod(str, Enum):
    NATIVE_PDF = "native_pdf"
    OCR = "ocr"
    HYBRID = "hybrid"
    FAILED = "failed"


class PageUnit(BaseModel):
    """Normalized extraction payload for a single PDF page."""

    schema_version: Literal["1.0"] = Field(default="1.0")
    page_number: int = Field(default=1)
    extraction_method: ExtractionMethod = Field(default=ExtractionMethod.FAILED)

    text_blocks: list[TextBlock] = Field(default_factory=list)
    tables: list[TableUnit] = Field(default_factory=list)
    images: list[ImageRegion] = Field(default_factory=list)

    image_count: int = Field(default=0)
    images_present: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_page_number(self) -> "PageUnit":
        if self.page_number < 1:
            raise ValueError("page_number must be >= 1")
        return self

    @model_validator(mode="after")
    def validate_image_flags(self) -> "PageUnit":
        if self.image_count > 0 and not self.images_present:
            raise ValueError("images_present must be True when image_count > 0")
        return self
