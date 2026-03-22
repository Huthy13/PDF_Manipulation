"""Structured debug artifacts emitted by the page-unit pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DocumentLifecycleEvent:
    """Document-scope lifecycle event."""

    event: str
    page_count: int
    source_doc_id: str


@dataclass(frozen=True, slots=True)
class PageDebugArtifact:
    """Page-scope debug details used for observability and test assertions."""

    page_number: int
    width: float
    height: float
    mode_decision: str
    mode_rationale: tuple[str, ...] = ()
    ocr_applied: bool = False
    table_success: bool = True
    table_error: str | None = None
    image_count: int = 0
    images_present: bool = False
    validation_success: bool = True
    validation_issue_count: int = 0
    warning_count: int = 0
    error: str | None = None
    page_start: bool = True
    page_end: bool = True


@dataclass(frozen=True, slots=True)
class FinalSummaryCounters:
    """Run-level counters reported at the end of processing."""

    native: int = 0
    ocr: int = 0
    hybrid: int = 0
    warnings: int = 0
    errors: int = 0
    tables: int = 0
    images: int = 0


@dataclass(frozen=True, slots=True)
class DocumentDebugArtifact:
    """Complete structured debug payload for one pipeline run."""

    document_events: tuple[DocumentLifecycleEvent, ...] = ()
    pages: tuple[PageDebugArtifact, ...] = ()
    final_summary: FinalSummaryCounters = field(default_factory=FinalSummaryCounters)

    def to_serializable(self) -> dict[str, Any]:
        """Return a deterministic dict shape useful for snapshots/fixtures."""

        return _stable(asdict(self))


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _stable(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_stable(item) for item in value]
    return value
