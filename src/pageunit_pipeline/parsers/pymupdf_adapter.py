"""PyMuPDF-backed PDF parser adapter for page-unit raw extraction."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

import fitz

from pageunit_pipeline.adapters.interfaces import PdfParserAdapter
from pageunit_pipeline.adapters.types import (
    EmbeddedImageReference,
    PageDimensions,
    RawBlockCandidate,
    RawLineCandidate,
    RawPageExtraction,
)
from pageunit_pipeline.models.bbox import BBox


class PyMuPdfParserAdapter(PdfParserAdapter):
    """Extract text/layout/image candidates from a PDF page via PyMuPDF."""

    _PARSER_NAME = "pymupdf"

    def __init__(self) -> None:
        self._parser_version = self._resolve_parser_version()

    @property
    def provider_name(self) -> str:
        return self._PARSER_NAME

    @property
    def provider_version(self) -> str:
        return self._parser_version

    def extract_page(self, *, pdf_bytes: bytes, page_number: int) -> RawPageExtraction:
        if page_number < 1:
            raise ValueError("page_number must be >= 1")

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if page_number > doc.page_count:
                raise ValueError(
                    f"page_number {page_number} exceeds page_count {doc.page_count}"
                )

            page = doc.load_page(page_number - 1)
            page_rect = page.rect
            page_width = float(page_rect.width)
            page_height = float(page_rect.height)

            full_text_candidate = page.get_text("text")
            text_dict = page.get_text("dict", sort=True)
            image_list = page.get_images(full=True)

            block_candidates, line_candidates, image_refs = self._extract_candidates(
                text_dict=text_dict,
                page_height=page_height,
            )
            if image_list:
                image_refs = self._merge_xref_refs(
                    image_refs=image_refs,
                    image_list=image_list,
                )

            provider_metadata = {
                "parser_name": self.provider_name,
                "parser_version": self.provider_version,
                "image_count_candidates": len(image_refs),
                "raw_provider_data": {
                    "text_dict": text_dict,
                    "image_list": image_list,
                },
            }

            return RawPageExtraction(
                page_number=page_number,
                page_dimensions=PageDimensions(width=page_width, height=page_height),
                full_text_candidate=full_text_candidate,
                block_candidates=tuple(block_candidates),
                line_candidates=tuple(line_candidates),
                embedded_image_references=tuple(image_refs),
                provider_metadata=provider_metadata,
                provider_name=self.provider_name,
                provider_version=self.provider_version,
            )

    def _extract_candidates(
        self,
        *,
        text_dict: dict[str, Any],
        page_height: float,
    ) -> tuple[list[RawBlockCandidate], list[RawLineCandidate], list[EmbeddedImageReference]]:
        block_candidates: list[RawBlockCandidate] = []
        line_candidates: list[RawLineCandidate] = []
        image_refs: list[EmbeddedImageReference] = []

        for reading_order, block in enumerate(text_dict.get("blocks", [])):
            block_type = int(block.get("type", 0))
            raw_bbox = block.get("bbox")
            block_bbox = self._normalize_bbox(
                raw_bbox=raw_bbox,
                page_height=page_height,
                source_origin="top-left",
            )

            if block_type == 0:
                block_lines: list[RawLineCandidate] = []
                for raw_line in block.get("lines", []):
                    line_text = self._line_text(raw_line)
                    if not line_text:
                        continue
                    line_bbox = self._normalize_bbox(
                        raw_bbox=raw_line.get("bbox"),
                        page_height=page_height,
                        source_origin="top-left",
                    )
                    line_candidate = RawLineCandidate(text=line_text, bbox=line_bbox)
                    block_lines.append(line_candidate)
                    line_candidates.append(line_candidate)

                block_text = "\n".join(line.text for line in block_lines).strip()
                if not block_text:
                    continue

                block_candidates.append(
                    RawBlockCandidate(
                        text=block_text,
                        bbox=block_bbox,
                        lines=tuple(block_lines),
                        reading_order=reading_order,
                    )
                )
            elif block_type == 1:
                image_id = str(block.get("number", reading_order))
                image_refs.append(
                    EmbeddedImageReference(
                        image_id=f"image-block-{image_id}",
                        bbox=block_bbox,
                    )
                )

        return block_candidates, line_candidates, image_refs

    def _merge_xref_refs(
        self,
        *,
        image_refs: list[EmbeddedImageReference],
        image_list: list[tuple[Any, ...]],
    ) -> list[EmbeddedImageReference]:
        existing_ids = {ref.image_id for ref in image_refs}
        merged = list(image_refs)

        for image_data in image_list:
            xref = image_data[0]
            mime = image_data[7] if len(image_data) > 7 else None
            image_id = f"image-xref-{xref}"
            if image_id in existing_ids:
                continue
            merged.append(
                EmbeddedImageReference(
                    image_id=image_id,
                    bbox=None,
                    mime_type=str(mime) if mime else None,
                )
            )
            existing_ids.add(image_id)

        return merged

    def _line_text(self, raw_line: dict[str, Any]) -> str:
        spans = raw_line.get("spans", [])
        return "".join(str(span.get("text", "")) for span in spans).strip()

    def _normalize_bbox(
        self,
        *,
        raw_bbox: Any,
        page_height: float,
        source_origin: str,
    ) -> BBox | None:
        if not raw_bbox or len(raw_bbox) != 4:
            return None

        x0, y0, x1, y1 = (float(raw_bbox[0]), float(raw_bbox[1]), float(raw_bbox[2]), float(raw_bbox[3]))

        if source_origin == "bottom-left":
            y0, y1 = page_height - y1, page_height - y0

        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        return BBox(x0=left, y0=top, x1=right, y1=bottom)

    def _resolve_parser_version(self) -> str:
        try:
            return version("PyMuPDF")
        except PackageNotFoundError:
            return fitz.VersionBind
