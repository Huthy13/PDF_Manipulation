"""Heuristic OCR decisioning for a single extracted page."""

from __future__ import annotations

from dataclasses import dataclass
from string import printable

from pageunit_pipeline.adapters.types import RawPageExtraction


@dataclass(frozen=True, slots=True)
class ExtractionDecision:
    """Decision payload describing how a page should be processed."""

    mode: str
    rationale: list[str]
    initial_confidence_hint: str


def choose_extraction_mode(raw_page: RawPageExtraction) -> ExtractionDecision:
    """Select ``native_pdf``, ``ocr``, or ``hybrid`` for one page.

    Signals considered:
    - low native text length
    - low printable-text density
    - high replacement-character/garbage ratio
    - image-heavy page with little selectable text
    """

    text = raw_page.full_text_candidate or ""
    stripped_text = text.strip()

    native_text_length = len(stripped_text)
    printable_count = sum(1 for char in text if char in printable)
    printable_density = _safe_ratio(printable_count, max(len(text), 1))

    replacement_count = text.count("\uFFFD")
    garbage_count = sum(1 for char in text if _looks_like_garbage(char))
    replacement_or_garbage_ratio = _safe_ratio(
        replacement_count + garbage_count,
        max(len(text), 1),
    )

    image_count = len(raw_page.embedded_image_references)
    image_heavy_with_little_text = image_count >= 2 and native_text_length < 120

    low_native_text = native_text_length < 80
    low_printable_density = printable_density < 0.80
    high_garbage_ratio = replacement_or_garbage_ratio > 0.08

    rationale: list[str] = []
    if low_native_text:
        rationale.append(
            f"Low native text length ({native_text_length} chars < 80-char threshold)."
        )
    if low_printable_density:
        rationale.append(
            "Low printable-text density "
            f"({printable_density:.2f} < 0.80 threshold)."
        )
    if high_garbage_ratio:
        rationale.append(
            "High replacement/garbage ratio "
            f"({replacement_or_garbage_ratio:.2f} > 0.08 threshold)."
        )
    if image_heavy_with_little_text:
        rationale.append(
            "Image-heavy page with little selectable text "
            f"({image_count} images, {native_text_length} chars)."
        )

    if image_heavy_with_little_text and (low_native_text or high_garbage_ratio):
        mode = "ocr"
        confidence_hint = "high"
    elif low_native_text and low_printable_density and high_garbage_ratio:
        mode = "ocr"
        confidence_hint = "high"
    elif high_garbage_ratio or (low_native_text and low_printable_density):
        mode = "hybrid"
        confidence_hint = "medium"
    else:
        mode = "native_pdf"
        confidence_hint = "high"

    if not rationale:
        rationale.append("Native selectable text quality appears sufficient.")

    return ExtractionDecision(
        mode=mode,
        rationale=rationale,
        initial_confidence_hint=confidence_hint,
    )


def _safe_ratio(value: int, total: int) -> float:
    return 0.0 if total <= 0 else value / total


def _looks_like_garbage(char: str) -> bool:
    if char.isspace():
        return False

    codepoint = ord(char)
    in_private_use_area = 0xE000 <= codepoint <= 0xF8FF
    in_control_range = codepoint < 32 and char not in {"\n", "\r", "\t"}

    return in_private_use_area or in_control_range
