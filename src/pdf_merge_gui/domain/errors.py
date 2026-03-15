from __future__ import annotations


class PdfLoadError(RuntimeError):
    """Raised when loading PDF source documents fails."""


class PdfSourceNotFoundError(PdfLoadError):
    """Raised when a PDF source path does not exist."""


class PdfMergeWriteError(RuntimeError):
    """Raised when writing the merged PDF fails."""

