from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageRef:
    source_path: str
    page_index: int
    display_name: str
