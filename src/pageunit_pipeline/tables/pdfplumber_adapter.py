"""pdfplumber-backed table detection/extraction helpers."""

from __future__ import annotations

import io
import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from pageunit_pipeline.models.bbox import BBox
from pageunit_pipeline.models.table import TableCell, TableUnit

LOGGER = logging.getLogger(__name__)


class PdfPlumberTableAdapter:
    """Detect and extract tables from a PDF page with pdfplumber.

    The adapter is intentionally fault-tolerant: on extraction failure it emits
    a warning and returns no tables so callers can continue with regular text
    extraction paths.
    """

    _PROVIDER_NAME = "pdfplumber"

    @property
    def provider_name(self) -> str:
        return self._PROVIDER_NAME

    @property
    def provider_version(self) -> str:
        try:
            return version("pdfplumber")
        except PackageNotFoundError:
            return "unknown"

    def extract_table_units(self, *, pdf_bytes: bytes, page_number: int) -> list[TableUnit]:
        """Return extracted :class:`TableUnit` objects for one 1-indexed page."""

        if page_number < 1:
            raise ValueError("page_number must be >= 1")

        try:
            import pdfplumber
        except ImportError:
            LOGGER.warning(
                "pdfplumber is not installed; table extraction skipped for page %s",
                page_number,
            )
            return []

        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                if page_number > len(pdf.pages):
                    raise ValueError(
                        f"page_number {page_number} exceeds page_count {len(pdf.pages)}"
                    )

                page = pdf.pages[page_number - 1]
                table_candidates = page.find_tables()
                units: list[TableUnit] = []

                for index, table in enumerate(table_candidates):
                    units.append(self._to_table_unit(table=table, index=index))

                return units
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "Table extraction failed on page %s via pdfplumber: %s. "
                "Continuing with page text path.",
                page_number,
                exc,
            )
            return []

    def detect_candidate_regions(self, *, pdf_bytes: bytes, page_number: int) -> list[BBox]:
        """Return candidate table regions for a 1-indexed page."""

        units = self.extract_table_units(pdf_bytes=pdf_bytes, page_number=page_number)
        return [unit.bbox for unit in units if unit.bbox is not None]

    def _to_table_unit(self, *, table: Any, index: int) -> TableUnit:
        raw_rows = self._extract_rows(table)
        n_rows = len(raw_rows)
        n_cols = max((len(row) for row in raw_rows), default=0)

        cells: list[TableCell] = []
        for row_index, row in enumerate(raw_rows):
            for col_index, value in enumerate(row):
                cells.append(
                    TableCell(
                        row_index=row_index,
                        col_index=col_index,
                        text="" if value is None else str(value).strip(),
                    )
                )

        header_row_indices, header_confidence = self._estimate_header_hints(raw_rows)

        raw_provider_data = {
            "table_index": index,
            "raw_bbox": getattr(table, "bbox", None),
            "n_rows_raw": n_rows,
            "n_cols_raw": n_cols,
            "raw_rows": raw_rows,
        }

        return TableUnit(
            title=f"Table {index + 1}",
            bbox=self._normalize_bbox(getattr(table, "bbox", None)),
            n_rows=n_rows,
            n_cols=n_cols,
            header_row_indices=header_row_indices,
            header_confidence=header_confidence,
            cells=cells,
            raw_provider_data=raw_provider_data,
        )

    def _extract_rows(self, table: Any) -> list[list[str | None]]:
        extracted: Any = None

        if hasattr(table, "extract"):
            extracted = table.extract()
        elif hasattr(table, "rows"):
            extracted = getattr(table, "rows")

        if not extracted:
            return []

        normalized: list[list[str | None]] = []
        for row in extracted:
            if row is None:
                continue
            if isinstance(row, (list, tuple)):
                normalized.append([
                    None if cell is None else str(cell) for cell in row
                ])
            else:
                normalized.append([str(row)])

        return normalized

    def _estimate_header_hints(self, rows: list[list[str | None]]) -> tuple[list[int], float | None]:
        if not rows:
            return [], None

        first_row = rows[0]
        non_empty = [str(cell).strip() for cell in first_row if cell and str(cell).strip()]
        if not non_empty:
            return [], 0.0

        mostly_text = sum(1 for value in non_empty if not _looks_numeric(value)) / len(non_empty)
        confidence = round(min(1.0, max(0.0, mostly_text)), 3)

        if confidence >= 0.55:
            return [0], confidence
        return [], confidence

    def _normalize_bbox(self, raw_bbox: Any) -> BBox | None:
        if not raw_bbox or len(raw_bbox) != 4:
            return None

        x0, top, x1, bottom = raw_bbox
        left, right = sorted((float(x0), float(x1)))
        y0, y1 = sorted((float(top), float(bottom)))
        return BBox(x0=left, y0=y0, x1=right, y1=y1)


def _looks_numeric(value: str) -> bool:
    compact = value.replace(",", "").replace("$", "").replace("%", "").strip()
    if not compact:
        return False

    try:
        float(compact)
        return True
    except ValueError:
        return False
