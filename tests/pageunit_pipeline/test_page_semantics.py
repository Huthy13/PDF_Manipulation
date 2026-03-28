from __future__ import annotations

from pageunit_pipeline.models.text import BlockType, QualityFlag, TextBlock
from pageunit_pipeline.normalize.page_semantics import normalize_page_semantics


def test_normalize_page_semantics_splits_noise_and_promotes_metadata() -> None:
    blocks = [
        TextBlock(block_type=BlockType.HEADER, text="ACME Inc 715-212-5396 Page 1 / 2", reading_order=0),
        TextBlock(block_type=BlockType.PARAGRAPH, text="Quotation: Q-1001", reading_order=1),
        TextBlock(block_type=BlockType.PARAGRAPH, text="Payment Terms: Net 30", reading_order=2),
        TextBlock(block_type=BlockType.PARAGRAPH, text="Scope includes Auto / Off / Manual sampling", reading_order=3),
    ]

    result = normalize_page_semantics(blocks)

    assert result.page_type == "quote"
    assert result.page_metadata["quotation_number"] == "Q-1001"
    assert result.page_metadata["payment_terms"] == "Net 30"
    assert len(result.noise_blocks) == 1
    assert any(flag == QualityFlag.FIELD_INFERRED_FROM_LAYOUT for flag in result.quality_flags)


def test_normalize_page_semantics_merges_fragments_and_infers_table() -> None:
    blocks = [
        TextBlock(block_type=BlockType.PARAGRAPH, text="Note 2:", reading_order=1),
        TextBlock(block_type=BlockType.PARAGRAPH, text="Relocation of magnet box as needed", reading_order=2),
        TextBlock(block_type=BlockType.PARAGRAPH, text="Description  Quantity  Unit Price  Amount", reading_order=3),
        TextBlock(block_type=BlockType.PARAGRAPH, text="Labor  1  100.00  100.00", reading_order=4),
    ]

    result = normalize_page_semantics(blocks)

    assert len(result.content_blocks) >= 2
    assert "Relocation of magnet box" in result.content_blocks[0].text
    assert result.inferred_tables
    assert result.inferred_tables[0].raw_provider_data["inferred"] is True
    assert QualityFlag.TABLE_STRUCTURE_IMPLICIT in result.quality_flags
