"""Document/page orchestration for the page-unit extraction pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import fitz

from pageunit_pipeline.builders.pageunit_builder import (
    PageUnitBuilder,
    candidate_from_any,
)
from pageunit_pipeline.debug.artifacts import (
    DocumentDebugArtifact,
    DocumentLifecycleEvent,
    FinalSummaryCounters,
    PageDebugArtifact,
)
from pageunit_pipeline.images.detector import HeuristicImageDetector
from pageunit_pipeline.models.page import ExtractionMethod, PageUnit
from pageunit_pipeline.models.text import LineUnit, TextBlock
from pageunit_pipeline.normalize.page_semantics import normalize_page_semantics
from pageunit_pipeline.normalize.structure_mapper import map_blocks_to_text_blocks
from pageunit_pipeline.normalize.text_normalizer import normalize_text
from pageunit_pipeline.ocr.decision import choose_extraction_mode
from pageunit_pipeline.ocr.tesseract_adapter import TesseractOcrAdapter
from pageunit_pipeline.parsers.pymupdf_adapter import PyMuPdfParserAdapter
from pageunit_pipeline.pipeline.intake import DocumentContext, validate_pdf_input
from pageunit_pipeline.tables.pdfplumber_adapter import PdfPlumberTableAdapter
from pageunit_pipeline.validators.page_validator import PageValidator, ValidationIssue


@dataclass(frozen=True, slots=True)
class PageProcessingArtifact:
    """One-page processing output including diagnostics and provenance."""

    page_unit: PageUnit
    decision_mode: str
    decision_rationale: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentSummaryStats:
    """Aggregated run-level counters for a document processing session."""

    total_pages: int
    extracted_pages: int
    failed_pages: int
    native_pages: int
    ocr_pages: int
    hybrid_pages: int
    total_tables: int
    total_images: int
    total_text_blocks: int
    total_validation_issues: int


@dataclass(frozen=True, slots=True)
class DocumentPipelineResult:
    """End-to-end result payload for one document pipeline run."""

    context: DocumentContext
    pages: tuple[PageProcessingArtifact, ...] = field(default_factory=tuple)
    summary: DocumentSummaryStats | None = None
    debug: DocumentDebugArtifact | None = None


class DocumentPipelineOrchestrator:
    """Orchestrates intake, per-page extraction, validation, and summary stats."""

    def __init__(
        self,
        *,
        parser: PyMuPdfParserAdapter | None = None,
        ocr_adapter: TesseractOcrAdapter | None = None,
        table_adapter: PdfPlumberTableAdapter | None = None,
        image_detector: HeuristicImageDetector | None = None,
        builder: PageUnitBuilder | None = None,
        validator: PageValidator | None = None,
    ) -> None:
        self.parser = parser or PyMuPdfParserAdapter()
        self.ocr_adapter = ocr_adapter or TesseractOcrAdapter(enabled=False)
        self.table_adapter = table_adapter or PdfPlumberTableAdapter()
        self.image_detector = image_detector or HeuristicImageDetector()
        self.builder = builder or PageUnitBuilder()
        self.validator = validator or PageValidator()

    def run(
        self,
        source: str | Path | bytes | bytearray | memoryview | BinaryIO,
        *,
        filename: str | None = None,
    ) -> DocumentPipelineResult:
        """Run the full document pipeline from intake through validation."""

        context = validate_pdf_input(source, filename=filename)
        page_artifacts: list[PageProcessingArtifact] = []
        debug_document_events: list[DocumentLifecycleEvent] = [
            DocumentLifecycleEvent(
                event="document_open",
                page_count=context.page_count,
                source_doc_id=context.source_doc_id,
            ),
            DocumentLifecycleEvent(
                event="document_start",
                page_count=context.page_count,
                source_doc_id=context.source_doc_id,
            ),
        ]
        debug_pages: list[PageDebugArtifact] = []

        with fitz.open(stream=context.pdf_bytes, filetype="pdf") as pdf_doc:
            for page_number in range(1, context.page_count + 1):
                try:
                    page_artifact, page_debug = self._process_single_page(
                        context=context,
                        page_number=page_number,
                        pdf_doc=pdf_doc,
                    )
                except Exception as exc:  # noqa: BLE001
                    page_artifact, page_debug = self._build_failed_page_artifact(
                        page_number=page_number,
                        pdf_doc=pdf_doc,
                        error=exc,
                    )
                page_artifacts.append(page_artifact)
                debug_pages.append(page_debug)

        summary = self._build_summary(page_artifacts, context.page_count)
        final_counters = self._build_final_debug_counters(page_artifacts)
        debug_document_events.append(
            DocumentLifecycleEvent(
                event="document_end",
                page_count=context.page_count,
                source_doc_id=context.source_doc_id,
            )
        )
        return DocumentPipelineResult(
            context=context,
            pages=tuple(page_artifacts),
            summary=summary,
            debug=DocumentDebugArtifact(
                document_events=tuple(debug_document_events),
                pages=tuple(debug_pages),
                final_summary=final_counters,
            ),
        )

    def _process_single_page(
        self,
        *,
        context: DocumentContext,
        page_number: int,
        pdf_doc: fitz.Document,
    ) -> tuple[PageProcessingArtifact, PageDebugArtifact]:
        page_obj = pdf_doc.load_page(page_number - 1)
        rect = page_obj.rect
        table_success = True
        table_error: str | None = None
        raw_page = self.parser.extract_page(pdf_bytes=context.pdf_bytes, page_number=page_number)
        decision = choose_extraction_mode(raw_page)

        native_page, table_success, table_error = self._build_native_candidate(raw_page, context.pdf_bytes)
        ocr_page = self._build_ocr_candidate(
            raw_page=raw_page,
            page_number=page_number,
            decision_mode=decision.mode,
            pdf_doc=pdf_doc,
            fallback_tables=native_page.tables,
            fallback_images=native_page.images,
        )
        hybrid_page = self._build_hybrid_candidate(
            native_page=native_page,
            ocr_page=ocr_page,
            decision_mode=decision.mode,
        )

        build_result = self.builder.build(
            [page_number],
            native={page_number: candidate_from_any(native_page, confidence=0.8)},
            ocr={page_number: candidate_from_any(ocr_page, confidence=0.7)} if ocr_page else None,
            hybrid={page_number: candidate_from_any(hybrid_page, confidence=0.85)} if hybrid_page else None,
        )

        built = build_result.pages[0]
        validation = self.validator.validate_pages([built.page_unit])
        validated_page = validation.pages[0]
        validation_success = all(issue.severity != "error" for issue in validation.issues)
        ocr_applied = decision.mode in {"ocr", "hybrid"} and ocr_page is not None

        return (
            PageProcessingArtifact(
                page_unit=validated_page,
                decision_mode=decision.mode,
                decision_rationale=tuple(decision.rationale),
                warnings=built.warnings,
                issues=validation.issues,
            ),
            PageDebugArtifact(
                page_number=page_number,
                width=float(rect.width),
                height=float(rect.height),
                mode_decision=decision.mode,
                mode_rationale=tuple(decision.rationale),
                ocr_applied=ocr_applied,
                table_success=table_success,
                table_error=table_error,
                image_count=validated_page.image_count,
                images_present=validated_page.images_present,
                validation_success=validation_success,
                validation_issue_count=len(validation.issues),
                warning_count=len(built.warnings),
                page_start=True,
                page_end=True,
            ),
        )

    def _build_native_candidate(self, raw_page, pdf_bytes: bytes) -> tuple[PageUnit, bool, str | None]:
        normalized_text = normalize_text(raw_page.full_text_candidate)
        text_blocks = map_blocks_to_text_blocks(
            raw_page.block_candidates,
            page_height=raw_page.page_dimensions.height,
            page_width=raw_page.page_dimensions.width,
        )

        if not text_blocks:
            text_blocks = [TextBlock(text=normalized_text.text)]
        semantic_result = normalize_page_semantics(text_blocks)

        table_success = True
        table_error: str | None = None
        try:
            tables = self.table_adapter.extract_table_units(
                pdf_bytes=pdf_bytes,
                page_number=raw_page.page_number,
            )
        except Exception as exc:  # noqa: BLE001
            tables = []
            table_success = False
            table_error = str(exc)
        image_detection = self.image_detector.detect(page=raw_page)
        merged_tables = list(tables) + list(semantic_result.inferred_tables)

        return (
            PageUnit(
                page_number=raw_page.page_number,
                extraction_method=ExtractionMethod.NATIVE_PDF,
                page_type=semantic_result.page_type,
                page_metadata=semantic_result.page_metadata,
                text_blocks=text_blocks,
                content_blocks=semantic_result.content_blocks,
                noise_blocks=semantic_result.noise_blocks,
                tables=merged_tables,
                images=image_detection.image_regions,
                image_count=image_detection.image_count,
                images_present=image_detection.images_present,
                quality_flags=semantic_result.quality_flags,
            ),
            table_success,
            table_error,
        )

    def _build_ocr_candidate(
        self,
        *,
        raw_page,
        page_number: int,
        decision_mode: str,
        pdf_doc: fitz.Document,
        fallback_tables,
        fallback_images,
    ) -> PageUnit | None:
        if decision_mode not in {"ocr", "hybrid"}:
            return None

        ocr_result = self.ocr_adapter.extract_from_page(
            page=pdf_doc.load_page(page_number - 1),
            page_number=page_number,
        )

        text = normalize_text(ocr_result.text).text
        if not text and ocr_result.provider_metadata.get("skipped", False):
            return None

        lines = [LineUnit(text=line.text) for line in ocr_result.line_boxes if line.text.strip()]
        block = TextBlock(text=text, lines=lines)
        semantic_result = normalize_page_semantics([block])

        return PageUnit(
            page_number=page_number,
            extraction_method=ExtractionMethod.OCR,
            text_blocks=[block],
            content_blocks=semantic_result.content_blocks,
            noise_blocks=semantic_result.noise_blocks,
            page_type=semantic_result.page_type,
            page_metadata=semantic_result.page_metadata,
            tables=list(fallback_tables),
            images=list(fallback_images),
            image_count=len(fallback_images),
            images_present=bool(fallback_images),
            quality_flags=semantic_result.quality_flags,
        )

    def _build_hybrid_candidate(
        self,
        *,
        native_page: PageUnit,
        ocr_page: PageUnit | None,
        decision_mode: str,
    ) -> PageUnit | None:
        if decision_mode != "hybrid" or ocr_page is None:
            return None

        merged_blocks = list(native_page.text_blocks)
        merged_blocks.extend(ocr_page.text_blocks)
        semantic_result = normalize_page_semantics(merged_blocks)

        return PageUnit(
            page_number=native_page.page_number,
            extraction_method=ExtractionMethod.HYBRID,
            page_type=semantic_result.page_type,
            page_metadata={**native_page.page_metadata, **ocr_page.page_metadata},
            text_blocks=merged_blocks,
            content_blocks=semantic_result.content_blocks,
            noise_blocks=semantic_result.noise_blocks,
            tables=native_page.tables,
            images=native_page.images,
            image_count=native_page.image_count,
            images_present=native_page.images_present,
            quality_flags=semantic_result.quality_flags,
        )

    def _build_summary(
        self,
        pages: list[PageProcessingArtifact],
        total_pages: int,
    ) -> DocumentSummaryStats:
        method_counts = {
            ExtractionMethod.NATIVE_PDF: 0,
            ExtractionMethod.OCR: 0,
            ExtractionMethod.HYBRID: 0,
            ExtractionMethod.FAILED: 0,
        }

        total_tables = 0
        total_images = 0
        total_text_blocks = 0
        total_issues = 0

        for artifact in pages:
            page = artifact.page_unit
            method_counts[page.extraction_method] += 1
            total_tables += len(page.tables)
            total_images += page.image_count
            total_text_blocks += len(page.text_blocks)
            total_issues += len(artifact.issues)

        failed_pages = method_counts[ExtractionMethod.FAILED]
        return DocumentSummaryStats(
            total_pages=total_pages,
            extracted_pages=total_pages - failed_pages,
            failed_pages=failed_pages,
            native_pages=method_counts[ExtractionMethod.NATIVE_PDF],
            ocr_pages=method_counts[ExtractionMethod.OCR],
            hybrid_pages=method_counts[ExtractionMethod.HYBRID],
            total_tables=total_tables,
            total_images=total_images,
            total_text_blocks=total_text_blocks,
            total_validation_issues=total_issues,
        )

    def _build_failed_page_artifact(
        self,
        *,
        page_number: int,
        pdf_doc: fitz.Document,
        error: Exception,
    ) -> tuple[PageProcessingArtifact, PageDebugArtifact]:
        rect = pdf_doc.load_page(page_number - 1).rect
        failed_page = PageUnit(
            page_number=page_number,
            extraction_method=ExtractionMethod.FAILED,
            text_blocks=[TextBlock(text="")],
            content_blocks=[],
        )
        validation = self.validator.validate_pages([failed_page])

        return (
            PageProcessingArtifact(
                page_unit=validation.pages[0],
                decision_mode="failed",
                decision_rationale=("Page processing failed; document processing continued.",),
                warnings=(str(error),),
                issues=validation.issues
                + (
                    ValidationIssue(
                        code="page_processing_error",
                        message=str(error),
                        page_number=page_number,
                    ),
                ),
            ),
            PageDebugArtifact(
                page_number=page_number,
                width=float(rect.width),
                height=float(rect.height),
                mode_decision="failed",
                mode_rationale=("Page processing failed; document processing continued.",),
                ocr_applied=False,
                table_success=False,
                table_error=str(error),
                image_count=0,
                images_present=False,
                validation_success=False,
                validation_issue_count=len(validation.issues) + 1,
                warning_count=1,
                error=str(error),
                page_start=True,
                page_end=True,
            ),
        )

    def _build_final_debug_counters(
        self,
        pages: list[PageProcessingArtifact],
    ) -> FinalSummaryCounters:
        return FinalSummaryCounters(
            native=sum(1 for page in pages if page.page_unit.extraction_method == ExtractionMethod.NATIVE_PDF),
            ocr=sum(1 for page in pages if page.page_unit.extraction_method == ExtractionMethod.OCR),
            hybrid=sum(1 for page in pages if page.page_unit.extraction_method == ExtractionMethod.HYBRID),
            warnings=sum(len(page.warnings) for page in pages),
            errors=sum(
                1
                for page in pages
                for issue in page.issues
                if issue.severity == "error"
            ),
            tables=sum(len(page.page_unit.tables) for page in pages),
            images=sum(page.page_unit.image_count for page in pages),
        )
