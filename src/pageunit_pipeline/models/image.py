"""Image region model.

Canonical coordinate convention:
- Origin is top-left of the page.
- x increases to the right, y increases downward.
"""

from pydantic import BaseModel, Field

from .bbox import BBox


class ImageRegion(BaseModel):
    """Image-like region detected on a page."""

    bbox: BBox | None = Field(default=None)
    label: str = Field(default="")
    confidence: float | None = Field(default=None)
    alt_text: str = Field(default="")
