"""Builder utilities for constructing canonical :class:`PageUnit` payloads.

The builder merges optional native/OCR/hybrid candidate outputs into one
``PageUnit`` per expected input page number while preserving extraction
provenance, confidence, and diagnostics.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pageunit_pipeline.models.page import ExtractionMethod, PageUnit


class CandidateSource(str, Enum):
    """Named extraction sources accepted by :class:`PageUnitBuilder`."""

    NATIVE = "native"
    OCR = "ocr"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class PageCandidate:
    """Input candidate payload from one extraction source."""

    page_number: int
    page_unit: PageUnit
    confidence: float | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PageUnitBuildArtifact:
    """Built ``PageUnit`` plus provenance metadata and diagnostics."""

    page_unit: PageUnit
    extraction_provenance: dict[str, str]
    extraction_confidence: float
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Result set from :meth:`PageUnitBuilder.build`."""

    pages: tuple[PageUnitBuildArtifact, ...] = field(default_factory=tuple)


class PageUnitBuilder:
    """Merge native/OCR/hybrid outputs into canonical page units."""

    def build(
        self,
        page_numbers: Sequence[int],
        *,
        native: Mapping[int, PageCandidate] | None = None,
        ocr: Mapping[int, PageCandidate] | None = None,
        hybrid: Mapping[int, PageCandidate] | None = None,
    ) -> BuildResult:
        """Build one artifact per requested page number.

        Resolution order prefers hybrid over native over OCR (when present).
        Missing pages still emit a ``PageUnit`` marked as ``FAILED``.
        """

        native_map = native or {}
        ocr_map = ocr or {}
        hybrid_map = hybrid or {}

        artifacts: list[PageUnitBuildArtifact] = []
        for page_number in page_numbers:
            artifact = self._build_single(
                page_number=page_number,
                native=native_map.get(page_number),
                ocr=ocr_map.get(page_number),
                hybrid=hybrid_map.get(page_number),
            )
            artifacts.append(artifact)

        return BuildResult(pages=tuple(artifacts))

    def _build_single(
        self,
        *,
        page_number: int,
        native: PageCandidate | None,
        ocr: PageCandidate | None,
        hybrid: PageCandidate | None,
    ) -> PageUnitBuildArtifact:
        candidates = {
            CandidateSource.NATIVE: native,
            CandidateSource.OCR: ocr,
            CandidateSource.HYBRID: hybrid,
        }

        selected_source = self._select_source(candidates)
        selected_candidate = candidates[selected_source] if selected_source else None

        if selected_candidate is None:
            failed_page = PageUnit(page_number=page_number, extraction_method=ExtractionMethod.FAILED)
            return PageUnitBuildArtifact(
                page_unit=failed_page,
                extraction_provenance={"selected_source": "none"},
                extraction_confidence=0.0,
                warnings=("No extraction output available for page.",),
                errors=("missing_extraction_candidates",),
            )

        canonical_page = selected_candidate.page_unit.model_copy(
            update={"page_number": page_number}
        )

        merged_warnings = self._collect_diagnostics("warnings", native, ocr, hybrid)
        merged_errors = self._collect_diagnostics("errors", native, ocr, hybrid)

        confidence = self._resolve_confidence(native=native, ocr=ocr, hybrid=hybrid)
        provenance = {
            "selected_source": selected_source.value,
            "native": "present" if native else "missing",
            "ocr": "present" if ocr else "missing",
            "hybrid": "present" if hybrid else "missing",
            "selected_method": canonical_page.extraction_method.value,
        }

        return PageUnitBuildArtifact(
            page_unit=canonical_page,
            extraction_provenance=provenance,
            extraction_confidence=confidence,
            warnings=merged_warnings,
            errors=merged_errors,
        )

    @staticmethod
    def _select_source(
        candidates: Mapping[CandidateSource, PageCandidate | None]
    ) -> CandidateSource | None:
        for source in (CandidateSource.HYBRID, CandidateSource.NATIVE, CandidateSource.OCR):
            if candidates.get(source) is not None:
                return source
        return None

    @staticmethod
    def _collect_diagnostics(
        kind: str,
        native: PageCandidate | None,
        ocr: PageCandidate | None,
        hybrid: PageCandidate | None,
    ) -> tuple[str, ...]:
        values: list[str] = []
        for source, candidate in (
            (CandidateSource.NATIVE, native),
            (CandidateSource.OCR, ocr),
            (CandidateSource.HYBRID, hybrid),
        ):
            if candidate is None:
                continue

            payload: tuple[str, ...] = getattr(candidate, kind)
            for item in payload:
                values.append(f"{source.value}:{item}")

        return tuple(values)

    @staticmethod
    def _resolve_confidence(
        *,
        native: PageCandidate | None,
        ocr: PageCandidate | None,
        hybrid: PageCandidate | None,
    ) -> float:
        weighted_confidence: list[float] = []
        for candidate, weight in ((hybrid, 0.5), (native, 0.3), (ocr, 0.2)):
            if candidate is None or candidate.confidence is None:
                continue
            bounded = min(1.0, max(0.0, candidate.confidence))
            weighted_confidence.append(bounded * weight)

        if not weighted_confidence:
            return 0.0

        confidence = sum(weighted_confidence)
        return round(min(1.0, max(0.0, confidence)), 4)


def candidate_from_any(payload: PageUnit | Mapping[str, Any], *, confidence: float | None = None) -> PageCandidate:
    """Create :class:`PageCandidate` from a ``PageUnit`` or mapping payload.

    This helper is intentionally permissive for adapter integration points.
    """

    if isinstance(payload, PageUnit):
        page_unit = payload
    else:
        page_unit = PageUnit.model_validate(payload)

    return PageCandidate(
        page_number=page_unit.page_number,
        page_unit=page_unit,
        confidence=confidence,
    )
