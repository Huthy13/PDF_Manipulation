"""Semantic normalization from parser-style text blocks into Page Unit content."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pageunit_pipeline.models.table import TableCell, TableUnit
from pageunit_pipeline.models.text import (
    BlockType,
    ContentRole,
    PageContentBlock,
    QualityFlag,
    TextBlock,
)

_CONTACT_RE = re.compile(r"(www\.|https?://|\b\d{3}[-.]\d{3}[-.]\d{4}\b|\bpage\s+\d+\s*/\s*\d+)", re.I)
_KEY_VALUE_RE = re.compile(r"^(quotation|quote|date|expiration|salesperson|total|payment terms|customer)\s*[:#-]?\s*(.+)$", re.I)
_HEADER_TABLE_HINT_RE = re.compile(r"\b(description|quantity|unit\s*price|amount|qty|total)\b", re.I)


@dataclass(frozen=True, slots=True)
class PageSemanticResult:
    page_type: str
    page_metadata: dict[str, str]
    content_blocks: list[PageContentBlock]
    noise_blocks: list[TextBlock]
    inferred_tables: list[TableUnit]
    quality_flags: list[QualityFlag]


def normalize_page_semantics(text_blocks: list[TextBlock]) -> PageSemanticResult:
    """Split noise/content, merge fragments, promote metadata, infer semantics."""

    ordered = sorted(text_blocks, key=lambda block: block.reading_order or 0)
    page_metadata: dict[str, str] = {}
    noise_blocks: list[TextBlock] = []
    content_candidates: list[TextBlock] = []
    quality_flags: list[QualityFlag] = []

    for block in ordered:
        text = block.text.strip()
        if not text:
            continue
        key_match = _KEY_VALUE_RE.match(_first_line(text))
        if key_match:
            key = _normalize_meta_key(key_match.group(1))
            if key and key not in page_metadata:
                page_metadata[key] = key_match.group(2).strip()
                quality_flags.append(QualityFlag.FIELD_INFERRED_FROM_LAYOUT)
        if _is_noise_block(block):
            noise_blocks.append(block)
        else:
            content_candidates.append(block)

    merged_content = _merge_content_blocks(content_candidates)
    content_blocks = [_to_content_block(block) for block in merged_content]
    inferred_tables = _infer_implicit_tables(merged_content)
    if inferred_tables:
        quality_flags.append(QualityFlag.TABLE_STRUCTURE_IMPLICIT)

    page_type = _classify_page_type(content_blocks, page_metadata)

    if not content_blocks:
        content_blocks = [PageContentBlock(role=ContentRole.GENERIC, text="")]

    return PageSemanticResult(
        page_type=page_type,
        page_metadata=page_metadata,
        content_blocks=content_blocks,
        noise_blocks=noise_blocks,
        inferred_tables=inferred_tables,
        quality_flags=_dedupe_flags(quality_flags),
    )


def _first_line(text: str) -> str:
    return text.splitlines()[0].strip() if text.splitlines() else text.strip()


def _normalize_meta_key(raw_key: str) -> str:
    normalized = raw_key.strip().lower().replace(" ", "_")
    aliases = {
        "quote": "quotation_number",
        "quotation": "quotation_number",
    }
    return aliases.get(normalized, normalized)


def _is_noise_block(block: TextBlock) -> bool:
    text = block.text.strip()
    if block.block_type in {BlockType.HEADER, BlockType.FOOTER}:
        return True
    return bool(_CONTACT_RE.search(text))


def _merge_content_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    if not blocks:
        return []

    merged: list[TextBlock] = []
    current = blocks[0].model_copy(deep=True)

    for candidate in blocks[1:]:
        if _should_merge(current, candidate):
            joiner = "\n" if current.text.endswith(":") else " "
            current.text = f"{current.text.rstrip()}{joiner}{candidate.text.lstrip()}".strip()
            current.lines.extend(candidate.lines)
            if current.reading_order is None:
                current.reading_order = candidate.reading_order
        else:
            merged.append(current)
            current = candidate.model_copy(deep=True)

    merged.append(current)
    return merged


def _should_merge(left: TextBlock, right: TextBlock) -> bool:
    left_text = left.text.strip()
    right_text = right.text.strip()
    if not left_text or not right_text:
        return False

    if left.block_type == right.block_type == BlockType.PARAGRAPH:
        return not left_text.endswith((".", "?", "!")) or right_text.lower().startswith(("note", "auto", "off", "manual"))

    if left_text.lower().startswith("note") and right.block_type in {BlockType.PARAGRAPH, BlockType.UNKNOWN}:
        return True

    return False


def _to_content_block(block: TextBlock) -> PageContentBlock:
    text = block.text.strip()
    role = _infer_role(text)
    block_flags: list[QualityFlag] = []

    if role == ContentRole.GENERIC:
        block_flags.append(QualityFlag.LOW_CONFIDENCE_ROLE_ASSIGNMENT)

    if role in {ContentRole.SCOPE_NARRATIVE, ContentRole.NOTE} and "note" in text.lower() and "scope" in text.lower():
        block_flags.append(QualityFlag.POSSIBLE_SCOPE_NOTE)

    if role in {ContentRole.LABOR, ContentRole.EQUIPMENT_DETAIL} and any(word in text.lower() for word in ("qty", "amount", "unit")):
        block_flags.append(QualityFlag.POSSIBLE_LINE_ITEM_DETAIL)

    return PageContentBlock(
        role=role,
        text=text,
        source_block_type=block.block_type,
        reading_order=block.reading_order,
        quality_flags=_dedupe_flags(block_flags),
    )


def _infer_role(text: str) -> ContentRole:
    lowered = text.lower()
    if any(token in lowered for token in ("quote", "quotation", "customer", "expiration", "salesperson")):
        return ContentRole.QUOTE_HEADER
    if "note" in lowered or "caveat" in lowered or "assumption" in lowered:
        return ContentRole.NOTE
    if "labor" in lowered:
        return ContentRole.LABOR
    if any(token in lowered for token in ("equipment", "magnet", "switch", "auto", "manual", "sampling")):
        return ContentRole.EQUIPMENT_DETAIL if "equipment" in lowered else ContentRole.SCOPE_NARRATIVE
    if any(token in lowered for token in ("total", "payment", "terms", "unit price", "amount")):
        return ContentRole.PRICING_TERMS
    if _HEADER_TABLE_HINT_RE.search(lowered):
        return ContentRole.TABLE_ROW
    return ContentRole.GENERIC


def _infer_implicit_tables(blocks: list[TextBlock]) -> list[TableUnit]:
    table_lines: list[str] = []
    for block in blocks:
        text = block.text.strip()
        if _HEADER_TABLE_HINT_RE.search(text) or "|" in text:
            table_lines.extend([line.strip() for line in text.splitlines() if line.strip()])

    if len(table_lines) < 2:
        return []

    cells: list[TableCell] = []
    max_cols = 0
    for row_idx, line in enumerate(table_lines):
        if "|" in line:
            cols = [col.strip() for col in line.split("|") if col.strip()]
        else:
            cols = re.split(r"\s{2,}", line)
            cols = [col.strip() for col in cols if col.strip()]
        max_cols = max(max_cols, len(cols))
        for col_idx, value in enumerate(cols):
            cells.append(TableCell(row_index=row_idx, col_index=col_idx, text=value))

    return [
        TableUnit(
            title="implicit_line_items",
            n_rows=len(table_lines),
            n_cols=max_cols,
            cells=cells,
            raw_provider_data={"inferred": True},
        )
    ]


def _classify_page_type(content_blocks: list[PageContentBlock], metadata: dict[str, str]) -> str:
    if "quotation_number" in metadata or any(block.role == ContentRole.QUOTE_HEADER for block in content_blocks):
        return "quote"
    if any(block.role == ContentRole.PRICING_TERMS for block in content_blocks):
        return "pricing"
    if any(block.role in {ContentRole.SCOPE_NARRATIVE, ContentRole.EQUIPMENT_DETAIL} for block in content_blocks):
        return "scope"
    return "unknown"


def _dedupe_flags(flags: list[QualityFlag]) -> list[QualityFlag]:
    seen: set[QualityFlag] = set()
    ordered: list[QualityFlag] = []
    for flag in flags:
        if flag in seen:
            continue
        seen.add(flag)
        ordered.append(flag)
    return ordered
