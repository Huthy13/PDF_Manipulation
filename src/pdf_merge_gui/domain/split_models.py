from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SplitMode(str, Enum):
    RANGE_LIST = "RANGE_LIST"
    EVERY_N = "EVERY_N"
    BOOKMARK = "BOOKMARK"
    SEPARATOR = "SEPARATOR"


@dataclass(frozen=True)
class SplitBoundary:
    start_page_index: int
    label: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class SplitNamingOptions:
    prefix: str = "split"
    zero_pad: int = 3
    include_labels: bool = True
    extension: str = ".pdf"


@dataclass(frozen=True)
class SplitPlan:
    mode: SplitMode
    boundaries: tuple[SplitBoundary, ...] = field(default_factory=tuple)
    naming_options: SplitNamingOptions = field(default_factory=SplitNamingOptions)


@dataclass(frozen=True)
class SplitOutputSpec:
    start_page_index: int
    end_page_index: int
    proposed_filename: str
