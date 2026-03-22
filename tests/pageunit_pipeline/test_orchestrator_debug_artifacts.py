from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from pageunit_pipeline.adapters.types import PageDimensions, RawPageExtraction
from pageunit_pipeline.models.image import ImageRegion
from pageunit_pipeline.models.page import ExtractionMethod, PageUnit
from pageunit_pipeline.models.table import TableCell, TableUnit
from pageunit_pipeline.ocr.tesseract_adapter import OcrLineBox, OcrPageResult
from pageunit_pipeline.pipeline.orchestrator import DocumentPipelineOrchestrator
from pageunit_pipeline.pipeline.serialize import write_pageunits_json


@pytest.fixture()
def sample_pdf_bytes() -> bytes:
    doc = fitz.open()
    page1 = doc.new_page(width=400, height=500)
    page1.insert_text((72, 72), "Page one native text")
    page2 = doc.new_page(width=300, height=600)
    page2.insert_text((72, 72), "")
    page3 = doc.new_page(width=500, height=400)
    page3.insert_text((72, 72), "Page three native text")
    payload = doc.tobytes()
    doc.close()
    return payload


class _FakeParser:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def extract_page(self, *, pdf_bytes: bytes, page_number: int) -> RawPageExtraction:
        del pdf_bytes
        self.calls.append(page_number)
        if page_number == 2:
            raise RuntimeError("synthetic parser failure for page 2")

        native_text = "A" * 150 if page_number == 1 else "B" * 140
        return RawPageExtraction(
            page_number=page_number,
            page_dimensions=PageDimensions(
                width=400.0 if page_number == 1 else 500.0,
                height=500.0 if page_number == 1 else 400.0,
            ),
            full_text_candidate=native_text,
            provider_metadata={},
        )


class _FakeParserOcr:
    def extract_page(self, *, pdf_bytes: bytes, page_number: int) -> RawPageExtraction:
        del pdf_bytes
        return RawPageExtraction(
            page_number=page_number,
            page_dimensions=PageDimensions(width=400.0, height=500.0),
            full_text_candidate="" if page_number == 1 else "C" * 120,
            provider_metadata={},
        )


class _FakeTableAdapter:
    def extract_table_units(self, *, pdf_bytes: bytes, page_number: int) -> list[TableUnit]:
        del pdf_bytes
        if page_number == 3:
            raise RuntimeError("synthetic table failure")
        if page_number == 1:
            return [
                TableUnit(
                    title="T1",
                    n_rows=1,
                    n_cols=1,
                    cells=[TableCell(row_index=0, col_index=0, text="ok")],
                )
            ]
        return []


class _FakeImageDetector:
    def detect(self, *, page: RawPageExtraction):
        count = 1 if page.page_number == 1 else 0
        return type(
            "ImageDetection",
            (),
            {
                "images_present": bool(count),
                "image_count": count,
                "image_regions": [ImageRegion(label="embedded_raster")] if count else [],
            },
        )()


class _FakeOcrAdapter:
    def extract_from_page(self, *, page: fitz.Page, page_number: int) -> OcrPageResult:
        del page
        return OcrPageResult(
            text=f"OCR text for page {page_number}",
            line_boxes=(OcrLineBox(text="OCR line", left=1, top=2, width=3, height=4),),
            provider_metadata={"enabled": True, "skipped": False},
        )


def test_one_pageunit_per_page_and_partial_failure_continues(sample_pdf_bytes: bytes) -> None:
    orchestrator = DocumentPipelineOrchestrator(
        parser=_FakeParser(),
        table_adapter=_FakeTableAdapter(),
        image_detector=_FakeImageDetector(),
    )

    result = orchestrator.run(sample_pdf_bytes, filename="sample.pdf")

    assert len(result.pages) == 3
    assert [artifact.page_unit.page_number for artifact in result.pages] == [1, 2, 3]
    assert result.pages[1].page_unit.extraction_method == ExtractionMethod.FAILED
    assert result.pages[2].page_unit.extraction_method == ExtractionMethod.NATIVE_PDF
    assert result.summary is not None
    assert result.summary.failed_pages == 1


def test_page_order_and_dimensions_are_preserved_in_debug(sample_pdf_bytes: bytes) -> None:
    orchestrator = DocumentPipelineOrchestrator(
        parser=_FakeParser(),
        table_adapter=_FakeTableAdapter(),
        image_detector=_FakeImageDetector(),
    )
    result = orchestrator.run(sample_pdf_bytes)
    assert result.debug is not None

    debug_pages = result.debug.pages
    assert [page.page_number for page in debug_pages] == [1, 2, 3]
    assert [(page.width, page.height) for page in debug_pages] == [
        (400.0, 500.0),
        (300.0, 600.0),
        (500.0, 400.0),
    ]


def test_ocr_triggered_page_path(sample_pdf_bytes: bytes) -> None:
    orchestrator = DocumentPipelineOrchestrator(
        parser=_FakeParserOcr(),
        ocr_adapter=_FakeOcrAdapter(),
        table_adapter=_FakeTableAdapter(),
        image_detector=_FakeImageDetector(),
    )
    result = orchestrator.run(sample_pdf_bytes)

    assert result.pages[0].decision_mode == "hybrid"
    assert result.pages[0].page_unit.extraction_method == ExtractionMethod.HYBRID
    assert result.debug is not None
    assert result.debug.pages[0].ocr_applied is True


def test_schema_compliance_and_deterministic_serialization_shape(tmp_path: Path) -> None:
    page2 = PageUnit(page_number=2, extraction_method=ExtractionMethod.NATIVE_PDF)
    page1 = PageUnit(page_number=1, extraction_method=ExtractionMethod.OCR)

    for page in (page1, page2):
        raw = page.model_dump_json()
        assert PageUnit.model_validate_json(raw).page_number == page.page_number

    output_path = tmp_path / "pageunits.json"
    write_pageunits_json([page2, page1], output_path=output_path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert [item["page_number"] for item in payload] == [1, 2]
    assert list(payload[0].keys()) == sorted(payload[0].keys())


def test_debug_logging_requirements_are_emitted(sample_pdf_bytes: bytes) -> None:
    orchestrator = DocumentPipelineOrchestrator(
        parser=_FakeParser(),
        table_adapter=_FakeTableAdapter(),
        image_detector=_FakeImageDetector(),
    )
    result = orchestrator.run(sample_pdf_bytes)
    assert result.debug is not None

    assert [event.event for event in result.debug.document_events] == [
        "document_open",
        "document_start",
        "document_end",
    ]
    assert all(page.page_start and page.page_end for page in result.debug.pages)
    assert all(page.mode_rationale for page in result.debug.pages)
    assert all(isinstance(page.ocr_applied, bool) for page in result.debug.pages)
    assert any(page.table_success is False for page in result.debug.pages)
    assert any(page.validation_success is False for page in result.debug.pages)

    counters = result.debug.final_summary
    assert counters.native >= 1
    assert counters.warnings >= 1
    assert counters.errors >= 1
    assert counters.tables >= 0
    assert counters.images >= 0
