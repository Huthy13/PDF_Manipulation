"""Validation helpers for canonical :class:`PageUnit` payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Callable

from pydantic import ValidationError

from pageunit_pipeline.models.bbox import BBox
from pageunit_pipeline.models.page import PageUnit
from pageunit_pipeline.models.text import TextBlock

RepairHook = Callable[[PageUnit], PageUnit]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A single validation issue associated with one page."""

    code: str
    message: str
    page_number: int | None = None
    severity: str = "error"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Validation output for one or more pages."""

    pages: tuple[PageUnit, ...]
    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return all(issue.severity != "error" for issue in self.issues)


class PageValidator:
    """Validate and optionally repair page-unit payloads before persistence."""

    def __init__(
        self,
        *,
        allow_repair: bool = True,
        repair_hooks: tuple[RepairHook, ...] = (),
    ) -> None:
        self.allow_repair = allow_repair
        self.repair_hooks = repair_hooks

    def validate_pages(self, pages: list[PageUnit]) -> ValidationResult:
        working = [page.model_copy(deep=True) for page in pages]
        issues: list[ValidationIssue] = []

        if self.allow_repair:
            working = [self._apply_default_repairs(page) for page in working]
            for hook in self.repair_hooks:
                working = [hook(page) for page in working]

        issues.extend(self._validate_sequential_page_numbers(working))

        for page in working:
            issues.extend(self._validate_text_exists(page))
            issues.extend(self._validate_images_and_tables(page))
            issues.extend(self._validate_coordinate_sanity(page))
            issues.extend(self._validate_schema_roundtrip(page))

        return ValidationResult(pages=tuple(working), issues=tuple(issues))

    @staticmethod
    def _apply_default_repairs(page: PageUnit) -> PageUnit:
        updates: dict[str, object] = {}

        if page.image_count != len(page.images):
            updates["image_count"] = len(page.images)

        expected_images_present = len(page.images) > 0
        if page.images_present != expected_images_present:
            updates["images_present"] = expected_images_present

        if not page.text_blocks:
            updates["text_blocks"] = [TextBlock(text="")]

        if updates:
            return page.model_copy(update=updates)
        return page

    @staticmethod
    def _validate_sequential_page_numbers(pages: list[PageUnit]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        expected = 1

        for page in sorted(pages, key=lambda item: item.page_number):
            if page.page_number < 1:
                issues.append(
                    ValidationIssue(
                        code="invalid_page_number",
                        page_number=page.page_number,
                        message="Page number must be positive.",
                    )
                )
            if page.page_number != expected:
                issues.append(
                    ValidationIssue(
                        code="non_sequential_page_number",
                        page_number=page.page_number,
                        message=(
                            f"Expected page_number={expected}, got {page.page_number}."
                        ),
                    )
                )
                expected = page.page_number
            expected += 1

        return issues

    @staticmethod
    def _validate_text_exists(page: PageUnit) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if not page.text_blocks:
            issues.append(
                ValidationIssue(
                    code="missing_text",
                    page_number=page.page_number,
                    message="At least one text block must be present (text may be empty).",
                )
            )
            return issues

        for block in page.text_blocks:
            if block.text is None:
                issues.append(
                    ValidationIssue(
                        code="invalid_text_field",
                        page_number=page.page_number,
                        message="TextBlock.text cannot be None.",
                    )
                )

        return issues

    @staticmethod
    def _validate_images_and_tables(page: PageUnit) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if page.image_count != len(page.images):
            issues.append(
                ValidationIssue(
                    code="image_count_mismatch",
                    page_number=page.page_number,
                    message="image_count must equal len(images).",
                )
            )

        if page.images_present != (len(page.images) > 0):
            issues.append(
                ValidationIssue(
                    code="images_present_mismatch",
                    page_number=page.page_number,
                    message="images_present must match whether images exist.",
                )
            )

        for table_index, table in enumerate(page.tables):
            max_row = max((cell.row_index for cell in table.cells), default=-1)
            max_col = max((cell.col_index for cell in table.cells), default=-1)

            if table.n_rows and max_row >= table.n_rows:
                issues.append(
                    ValidationIssue(
                        code="table_row_bounds",
                        page_number=page.page_number,
                        message=(
                            f"Table {table_index} has cell row_index={max_row} outside "
                            f"n_rows={table.n_rows}."
                        ),
                    )
                )

            if table.n_cols and max_col >= table.n_cols:
                issues.append(
                    ValidationIssue(
                        code="table_col_bounds",
                        page_number=page.page_number,
                        message=(
                            f"Table {table_index} has cell col_index={max_col} outside "
                            f"n_cols={table.n_cols}."
                        ),
                    )
                )

            if table.n_rows < 0 or table.n_cols < 0:
                issues.append(
                    ValidationIssue(
                        code="negative_table_dimensions",
                        page_number=page.page_number,
                        message=f"Table {table_index} dimensions must be non-negative.",
                    )
                )

        return issues

    def _validate_coordinate_sanity(self, page: PageUnit) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        bboxes: list[tuple[str, BBox | None]] = []
        for idx, image in enumerate(page.images):
            bboxes.append((f"image[{idx}]", image.bbox))
        for idx, table in enumerate(page.tables):
            bboxes.append((f"table[{idx}]", table.bbox))
            for cell_idx, cell in enumerate(table.cells):
                bboxes.append((f"table[{idx}].cell[{cell_idx}]", cell.bbox))
        for idx, block in enumerate(page.text_blocks):
            bboxes.append((f"text_blocks[{idx}]", block.bbox))
            for line_idx, line in enumerate(block.lines):
                bboxes.append((f"text_blocks[{idx}].lines[{line_idx}]", line.bbox))

        for label, bbox in bboxes:
            if bbox is None:
                continue

            for axis_name, value in (
                ("x0", bbox.x0),
                ("y0", bbox.y0),
                ("x1", bbox.x1),
                ("y1", bbox.y1),
            ):
                if not isfinite(value):
                    issues.append(
                        ValidationIssue(
                            code="non_finite_bbox_coordinate",
                            page_number=page.page_number,
                            message=f"{label}.{axis_name} must be finite.",
                        )
                    )

        return issues

    @staticmethod
    def _validate_schema_roundtrip(page: PageUnit) -> list[ValidationIssue]:
        try:
            serialized = page.model_dump_json()
            PageUnit.model_validate_json(serialized)
        except ValidationError as exc:
            return [
                ValidationIssue(
                    code="schema_roundtrip_failed",
                    page_number=page.page_number,
                    message=str(exc),
                )
            ]
        return []
