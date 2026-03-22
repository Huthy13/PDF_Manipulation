"""Input intake helpers for page-unit document processing.

This module normalizes multiple inbound source shapes (path, bytes, file-like),
validates that the input is a readable PDF payload, and builds a structured
:class:`DocumentContext` used by downstream pipeline stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from pypdf import PdfReader


class IntakeError(ValueError):
    """Raised when a source cannot be accepted by the intake pipeline."""


@dataclass(frozen=True, slots=True)
class FileMetadata:
    """Collected source metadata captured during intake."""

    filename: str | None
    filename_stem: str | None
    byte_size: int
    captured_at: datetime
    source_path: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DocumentContext:
    """Canonical intake payload for downstream pipeline modules."""

    source_doc_id: str
    checksum_sha256: str
    page_count: int
    processing_session_id: str
    metadata: FileMetadata
    pdf_bytes: bytes = field(repr=False)


def validate_pdf_input(
    source: str | Path | bytes | bytearray | memoryview | BinaryIO,
    *,
    filename: str | None = None,
    captured_at: datetime | None = None,
) -> DocumentContext:
    """Validate source input and build a normalized :class:`DocumentContext`.

    Args:
        source: Input source as filesystem path, bytes-like payload, or file-like
            object implementing ``read()``.
        filename: Optional filename override. Used for metadata and ID creation.
        captured_at: Optional metadata timestamp. Defaults to UTC now.

    Returns:
        Validated document context with bytes, checksum, metadata, and page count.

    Raises:
        IntakeError: If the source is unreadable, empty, not a PDF, or unparseable.
    """

    pdf_bytes, source_path, detected_name = _read_source_bytes(source)
    _validate_pdf_signature(pdf_bytes)

    checksum = sha256(pdf_bytes).hexdigest()
    metadata = collect_file_metadata(
        source=source,
        filename=filename or detected_name,
        source_path=source_path,
        byte_size=len(pdf_bytes),
        captured_at=captured_at,
    )
    source_doc_id = compute_source_doc_id(checksum, metadata.filename_stem)
    page_count = count_pages(pdf_bytes)
    processing_session_id = create_processing_session_id(source_doc_id)

    return DocumentContext(
        source_doc_id=source_doc_id,
        checksum_sha256=checksum,
        page_count=page_count,
        processing_session_id=processing_session_id,
        metadata=metadata,
        pdf_bytes=pdf_bytes,
    )


def compute_source_doc_id(
    checksum_sha256: str,
    filename_stem: str | None = None,
    *,
    checksum_prefix_length: int = 12,
) -> str:
    """Compute deterministic source document ID from checksum and filename stem."""

    if not checksum_sha256:
        raise IntakeError("Cannot compute source_doc_id: checksum is empty.")
    if checksum_prefix_length <= 0:
        raise IntakeError("checksum_prefix_length must be greater than zero.")

    normalized_stem = _sanitize_id_component(filename_stem or "document")
    prefix = checksum_sha256[:checksum_prefix_length]
    return f"{prefix}-{normalized_stem}"


def collect_file_metadata(
    source: str | Path | bytes | bytearray | memoryview | BinaryIO,
    *,
    filename: str | None,
    source_path: str | None = None,
    byte_size: int,
    captured_at: datetime | None = None,
) -> FileMetadata:
    """Collect standardized metadata for the input source."""

    resolved_filename = filename
    if resolved_filename is None and isinstance(source, (str, Path)):
        resolved_filename = Path(source).name

    filename_stem = Path(resolved_filename).stem if resolved_filename else None
    timestamp = captured_at or datetime.now(timezone.utc)

    return FileMetadata(
        filename=resolved_filename,
        filename_stem=filename_stem,
        byte_size=byte_size,
        captured_at=timestamp,
        source_path=source_path,
    )


def count_pages(pdf_bytes: bytes) -> int:
    """Count the number of pages in a PDF payload."""

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        return len(reader.pages)
    except Exception as exc:  # pragma: no cover - exact parser exceptions vary.
        raise IntakeError("Failed to read PDF page count from provided bytes.") from exc


def create_processing_session_id(source_doc_id: str) -> str:
    """Create a unique processing session ID scoped to a source document."""

    if not source_doc_id:
        raise IntakeError("Cannot create processing session ID without source_doc_id.")
    return f"{source_doc_id}:{uuid4().hex}"


def _read_source_bytes(
    source: str | Path | bytes | bytearray | memoryview | BinaryIO,
) -> tuple[bytes, str | None, str | None]:
    """Read source into bytes while preserving diagnostic context."""

    if isinstance(source, (str, Path)):
        source_path = str(Path(source))
        try:
            payload = Path(source).read_bytes()
        except OSError as exc:
            raise IntakeError(f"Unable to read PDF from path: {source_path}") from exc
        if not payload:
            raise IntakeError(f"PDF payload is empty: {source_path}")
        return payload, source_path, Path(source).name

    if isinstance(source, (bytes, bytearray, memoryview)):
        payload = bytes(source)
        if not payload:
            raise IntakeError("PDF payload is empty.")
        return payload, None, None

    reader = getattr(source, "read", None)
    if callable(reader):
        try:
            payload = reader()
        except Exception as exc:
            raise IntakeError("Failed to read PDF from file-like source.") from exc

        if isinstance(payload, str):
            raise IntakeError("File-like source returned text; expected binary bytes.")
        if payload is None:
            raise IntakeError("File-like source returned no data.")

        payload_bytes = bytes(payload)
        if not payload_bytes:
            raise IntakeError("PDF payload is empty.")

        stream_name = _extract_stream_name(source)
        return payload_bytes, stream_name, Path(stream_name).name if stream_name else None

    raise IntakeError(
        "Unsupported source type. Expected path, bytes-like object, or file-like object."
    )


def _validate_pdf_signature(pdf_bytes: bytes) -> None:
    """Validate the PDF header signature.

    The PDF specification requires a header beginning with ``%PDF-`` near the
    beginning of the file. We accept it in the first 1024 bytes.
    """

    header_window = pdf_bytes[:1024]
    if b"%PDF-" not in header_window:
        raise IntakeError("Invalid PDF signature: missing %PDF- header in first 1024 bytes.")


def _extract_stream_name(stream: BinaryIO) -> str | None:
    """Best-effort stream name extraction for metadata."""

    name = getattr(stream, "name", None)
    return str(name) if name else None


def _sanitize_id_component(value: str) -> str:
    sanitized = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    return sanitized or "document"
