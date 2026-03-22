"""Pipeline modules for pageunit processing."""

from .intake import (
    DocumentContext,
    FileMetadata,
    IntakeError,
    collect_file_metadata,
    compute_source_doc_id,
    count_pages,
    create_processing_session_id,
    validate_pdf_input,
)
from .orchestrator import (
    DocumentPipelineOrchestrator,
    DocumentPipelineResult,
    DocumentSummaryStats,
    PageProcessingArtifact,
)
from .serialize import (
    serialize_pageunits,
    write_pageunits_json,
    write_pageunits_ndjson,
)

__all__ = [
    "DocumentContext",
    "FileMetadata",
    "IntakeError",
    "validate_pdf_input",
    "compute_source_doc_id",
    "collect_file_metadata",
    "count_pages",
    "create_processing_session_id",
    "DocumentPipelineOrchestrator",
    "DocumentPipelineResult",
    "PageProcessingArtifact",
    "DocumentSummaryStats",
    "write_pageunits_json",
    "write_pageunits_ndjson",
    "serialize_pageunits",
]
