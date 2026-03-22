"""Bounding box model.

Canonical coordinate convention:
- Origin is top-left of the page.
- x increases to the right, y increases downward.
"""

from pydantic import BaseModel, Field, model_validator


class BBox(BaseModel):
    """Axis-aligned bounding box using top-left origin coordinates."""

    x0: float = Field(default=0.0)
    y0: float = Field(default=0.0)
    x1: float = Field(default=0.0)
    y1: float = Field(default=0.0)

    @model_validator(mode="after")
    def validate_coordinate_order(self) -> "BBox":
        if self.x1 < self.x0:
            raise ValueError("Invalid bbox: x1 must be >= x0")
        if self.y1 < self.y0:
            raise ValueError("Invalid bbox: y1 must be >= y0")
        return self
