"""Mapping utilities from parser/OCR raw outputs into canonical text models."""

from __future__ import annotations

from typing import Any, Iterable

from pageunit_pipeline.adapters.types import RawBlockCandidate, RawLineCandidate
from pageunit_pipeline.models.bbox import BBox
from pageunit_pipeline.models.text import BlockType, LineUnit, TextBlock


def map_blocks_to_text_blocks(
    blocks: Iterable[RawBlockCandidate | dict[str, Any]],
    *,
    page_height: float | None = None,
    page_width: float | None = None,
) -> list[TextBlock]:
    """Map parser/OCR block outputs to :class:`TextBlock` instances.

    Classification is layout-heuristic only and intentionally avoids semantic
    interpretation beyond common position/shape signals.
    """

    mapped: list[TextBlock] = []
    for fallback_order, raw_block in enumerate(blocks):
        block_text = _extract_text(raw_block)
        lines = map_lines_to_line_units(_extract_lines(raw_block))
        bbox = _extract_bbox(raw_block)
        reading_order = _extract_reading_order(raw_block, fallback_order)

        if not block_text and lines:
            block_text = "\n".join(line.text for line in lines if line.text).strip()

        block_type = _classify_block_type(
            text=block_text,
            lines=lines,
            bbox=bbox,
            page_height=page_height,
            page_width=page_width,
        )

        mapped.append(
            TextBlock(
                block_type=block_type,
                text=block_text,
                lines=lines,
                bbox=bbox,
                reading_order=reading_order,
            )
        )

    return mapped


def map_lines_to_line_units(
    lines: Iterable[RawLineCandidate | dict[str, Any]],
) -> list[LineUnit]:
    """Map parser/OCR line outputs to :class:`LineUnit` values."""

    mapped_lines: list[LineUnit] = []
    for raw_line in lines:
        text = _extract_text(raw_line)
        if not text:
            continue

        mapped_lines.append(
            LineUnit(
                text=text,
                bbox=_extract_bbox(raw_line),
                confidence=_extract_confidence(raw_line),
            )
        )

    return mapped_lines


def _extract_text(candidate: RawBlockCandidate | RawLineCandidate | dict[str, Any]) -> str:
    if isinstance(candidate, dict):
        return str(candidate.get("text", "")).strip()
    return str(getattr(candidate, "text", "")).strip()


def _extract_bbox(candidate: Any) -> BBox | None:
    if isinstance(candidate, dict):
        raw_bbox = candidate.get("bbox")
    else:
        raw_bbox = getattr(candidate, "bbox", None)

    if isinstance(raw_bbox, BBox) or raw_bbox is None:
        return raw_bbox

    if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4:
        x0, y0, x1, y1 = raw_bbox
        return BBox(x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1))

    return None


def _extract_lines(block: RawBlockCandidate | dict[str, Any]) -> Iterable[RawLineCandidate | dict[str, Any]]:
    if isinstance(block, dict):
        return block.get("lines", []) or []
    return getattr(block, "lines", ()) or ()


def _extract_reading_order(block: RawBlockCandidate | dict[str, Any], fallback: int) -> int:
    if isinstance(block, dict):
        order = block.get("reading_order")
    else:
        order = getattr(block, "reading_order", None)

    return int(order) if order is not None else fallback


def _extract_confidence(line: RawLineCandidate | dict[str, Any]) -> float | None:
    if isinstance(line, dict):
        value = line.get("confidence")
    else:
        value = getattr(line, "confidence", None)

    if value is None:
        return None

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None

    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


def _classify_block_type(
    *,
    text: str,
    lines: list[LineUnit],
    bbox: BBox | None,
    page_height: float | None,
    page_width: float | None,
) -> BlockType:
    if not text:
        return BlockType.UNKNOWN

    line_count = len(lines) if lines else max(1, text.count("\n") + 1)
    stripped = text.strip()

    if _looks_like_table_text(stripped):
        return BlockType.TABLE_TEXT

    if bbox and page_height and _is_header_region(bbox, page_height):
        return BlockType.HEADER

    if bbox and page_height and _is_footer_region(bbox, page_height):
        return BlockType.FOOTER

    if _looks_like_heading(stripped, line_count, bbox=bbox, page_width=page_width):
        return BlockType.HEADING

    if line_count >= 1:
        return BlockType.PARAGRAPH

    return BlockType.UNKNOWN


def _is_header_region(bbox: BBox, page_height: float) -> bool:
    return bbox.y1 <= page_height * 0.12


def _is_footer_region(bbox: BBox, page_height: float) -> bool:
    return bbox.y0 >= page_height * 0.88


def _looks_like_heading(
    text: str,
    line_count: int,
    *,
    bbox: BBox | None,
    page_width: float | None,
) -> bool:
    if line_count > 2:
        return False

    words = [word for word in text.split() if word]
    if not words:
        return False

    short_block = len(words) <= 12
    title_case_ratio = sum(1 for w in words if w[:1].isupper()) / len(words)
    punctuation_light = text.count(".") <= 1

    narrow_width = False
    if bbox and page_width and page_width > 0:
        narrow_width = (bbox.x1 - bbox.x0) <= page_width * 0.7

    return short_block and punctuation_light and (title_case_ratio >= 0.6 or narrow_width)


def _looks_like_table_text(text: str) -> bool:
    if "\t" in text:
        return True

    lines = [line for line in text.split("\n") if line.strip()]
    if len(lines) < 2:
        return False

    delimiter_hits = 0
    for line in lines[:6]:
        tokens = [token for token in line.replace("|", " ").split() if token]
        numeric_tokens = sum(
            1 for token in tokens if token.replace(",", "").replace(".", "").isdigit()
        )
        if "|" in line or (len(tokens) >= 3 and numeric_tokens >= 2):
            delimiter_hits += 1

    return delimiter_hits >= 2
