"""Adapter interfaces and intermediate raw payload datatypes."""

from .interfaces import (
    ImageDetectorAdapter,
    OcrAdapter,
    PdfParserAdapter,
    TableExtractorAdapter,
)
from .types import (
    EmbeddedImageReference,
    PageDimensions,
    RawBlockCandidate,
    RawImageDetection,
    RawLineCandidate,
    RawPageExtraction,
    RawTableCandidate,
    RawTableExtraction,
)

__all__ = [
    "EmbeddedImageReference",
    "ImageDetectorAdapter",
    "OcrAdapter",
    "PageDimensions",
    "PdfParserAdapter",
    "RawBlockCandidate",
    "RawImageDetection",
    "RawLineCandidate",
    "RawPageExtraction",
    "RawTableCandidate",
    "RawTableExtraction",
    "TableExtractorAdapter",
]
