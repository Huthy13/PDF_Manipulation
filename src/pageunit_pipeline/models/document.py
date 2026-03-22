"""Document-level wrapper around page units.

Canonical coordinate convention:
- Origin is top-left of the page.
- x increases to the right, y increases downward.
"""

from pydantic import BaseModel, Field

from .page import PageUnit


class DocumentUnit(BaseModel):
    """Optional wrapper for a full multi-page extraction result."""

    source_path: str = Field(default="")
    source_id: str = Field(default="")
    pages: list[PageUnit] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
