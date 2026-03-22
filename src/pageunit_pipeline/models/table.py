"""Table-related models.

Canonical coordinate convention:
- Origin is top-left of the page.
- x increases to the right, y increases downward.
"""

from pydantic import BaseModel, Field

from .bbox import BBox


class TableCell(BaseModel):
    """Extracted table cell payload."""

    row_index: int = Field(default=0)
    col_index: int = Field(default=0)
    row_span: int = Field(default=1)
    col_span: int = Field(default=1)
    text: str = Field(default="")
    bbox: BBox | None = Field(default=None)
    confidence: float | None = Field(default=None)


class TableUnit(BaseModel):
    """Structured table extraction on a page."""

    title: str = Field(default="")
    bbox: BBox | None = Field(default=None)
    n_rows: int = Field(default=0)
    n_cols: int = Field(default=0)
    cells: list[TableCell] = Field(default_factory=list)
