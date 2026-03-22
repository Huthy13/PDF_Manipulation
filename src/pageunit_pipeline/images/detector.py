"""Heuristic image detection for page-unit pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pageunit_pipeline.adapters.types import EmbeddedImageReference, RawPageExtraction
from pageunit_pipeline.models.bbox import BBox
from pageunit_pipeline.models.image import ImageRegion


@dataclass(frozen=True, slots=True)
class ImageDetectionResult:
    """Normalized image summary for one page."""

    images_present: bool
    image_count: int
    image_regions: list[ImageRegion]


class HeuristicImageDetector:
    """Detect non-text image presence/regions from parser extraction payloads."""

    def detect(self, *, page: RawPageExtraction) -> ImageDetectionResult:
        refs = list(page.embedded_image_references)
        if not refs:
            refs = self._infer_refs_from_provider_metadata(page)

        regions: list[ImageRegion] = [
            self._to_image_region(ref=ref, page_width=page.page_dimensions.width, page_height=page.page_dimensions.height)
            for ref in refs
        ]

        return ImageDetectionResult(
            images_present=bool(regions),
            image_count=len(regions),
            image_regions=regions,
        )

    def _infer_refs_from_provider_metadata(self, page: RawPageExtraction) -> list[EmbeddedImageReference]:
        text_dict = page.provider_metadata.get("raw_provider_data", {}).get("text_dict", {})
        blocks = text_dict.get("blocks", []) if isinstance(text_dict, dict) else []

        inferred: list[EmbeddedImageReference] = []
        for idx, block in enumerate(blocks):
            if int(block.get("type", 0)) != 1:
                continue

            inferred.append(
                EmbeddedImageReference(
                    image_id=f"inferred-image-{idx}",
                    bbox=self._normalize_bbox(block.get("bbox")),
                )
            )

        return inferred

    def _to_image_region(
        self,
        *,
        ref: EmbeddedImageReference,
        page_width: float,
        page_height: float,
    ) -> ImageRegion:
        label, confidence = self._classify_region(
            bbox=ref.bbox,
            page_width=page_width,
            page_height=page_height,
        )

        return ImageRegion(
            bbox=ref.bbox,
            label=label,
            confidence=confidence,
            alt_text="",
        )

    def _classify_region(
        self,
        *,
        bbox: BBox | None,
        page_width: float,
        page_height: float,
    ) -> tuple[str, float]:
        if bbox is None:
            return "unknown", 0.4

        page_area = max(page_width * page_height, 1.0)
        area_ratio = _bbox_area(bbox) / page_area

        if area_ratio >= 0.9:
            return "full_page_scan", 0.9

        near_top = bbox.y0 <= page_height * 0.25
        small = area_ratio <= 0.05
        if small and near_top:
            return "logo/seal", 0.75

        if area_ratio > 0.0:
            return "embedded_raster", 0.8

        return "unknown", 0.4

    def _normalize_bbox(self, raw_bbox: Iterable[float] | None) -> BBox | None:
        if raw_bbox is None:
            return None

        values = list(raw_bbox)
        if len(values) != 4:
            return None

        x0, y0, x1, y1 = values
        left, right = sorted((float(x0), float(x1)))
        top, bottom = sorted((float(y0), float(y1)))
        return BBox(x0=left, y0=top, x1=right, y1=bottom)


def _bbox_area(bbox: BBox) -> float:
    return max(0.0, bbox.x1 - bbox.x0) * max(0.0, bbox.y1 - bbox.y0)
