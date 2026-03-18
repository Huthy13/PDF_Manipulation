from __future__ import annotations

import re
from collections.abc import Sequence

from ..domain import SplitBoundary, SplitMode, SplitNamingOptions, SplitOutputSpec, SplitPlan


class SplitService:
    @staticmethod
    def parse_mode(mode: SplitMode | str) -> SplitMode:
        if isinstance(mode, SplitMode):
            return mode

        normalized = mode.strip().replace("-", "_").replace(" ", "_").upper()
        try:
            return SplitMode(normalized)
        except ValueError as exc:
            raise ValueError(f"Unsupported split mode: {mode}") from exc

    @classmethod
    def compute_boundaries(
        cls,
        *,
        mode: SplitMode | str,
        page_count: int,
        range_starts: Sequence[int] | None = None,
        every_n: int | None = None,
        bookmark_starts: Sequence[SplitBoundary | int] | None = None,
        separator_starts: Sequence[SplitBoundary | int] | None = None,
    ) -> tuple[SplitBoundary, ...]:
        if page_count <= 0:
            raise ValueError("page_count must be greater than zero")

        parsed_mode = cls.parse_mode(mode)

        if parsed_mode == SplitMode.RANGE_LIST:
            return cls._normalized_boundaries(page_count, range_starts or [0])

        if parsed_mode == SplitMode.EVERY_N:
            if every_n is None or every_n <= 0:
                raise ValueError("every_n must be a positive integer for EVERY_N mode")
            return tuple(SplitBoundary(start_page_index=idx) for idx in range(0, page_count, every_n))

        if parsed_mode == SplitMode.BOOKMARK:
            return cls._normalized_boundaries(page_count, bookmark_starts or [0])

        if parsed_mode == SplitMode.SEPARATOR:
            return cls._normalized_boundaries(page_count, separator_starts or [0])

        raise ValueError(f"Unsupported split mode: {parsed_mode}")

    @classmethod
    def build_plan(
        cls,
        *,
        mode: SplitMode | str,
        page_count: int,
        naming_options: SplitNamingOptions | None = None,
        range_starts: Sequence[int] | None = None,
        every_n: int | None = None,
        bookmark_starts: Sequence[SplitBoundary | int] | None = None,
        separator_starts: Sequence[SplitBoundary | int] | None = None,
    ) -> SplitPlan:
        boundaries = cls.compute_boundaries(
            mode=mode,
            page_count=page_count,
            range_starts=range_starts,
            every_n=every_n,
            bookmark_starts=bookmark_starts,
            separator_starts=separator_starts,
        )
        return SplitPlan(
            mode=cls.parse_mode(mode),
            boundaries=boundaries,
            naming_options=naming_options or SplitNamingOptions(),
        )

    @staticmethod
    def emit_output_specs(plan: SplitPlan, *, page_count: int) -> list[SplitOutputSpec]:
        if page_count <= 0:
            raise ValueError("page_count must be greater than zero")
        if not plan.boundaries:
            raise ValueError("Split plan must include at least one boundary")

        starts = [boundary.start_page_index for boundary in plan.boundaries]
        if starts != sorted(set(starts)):
            raise ValueError("Split boundaries must be unique and sorted in ascending order")

        outputs: list[SplitOutputSpec] = []
        for idx, boundary in enumerate(plan.boundaries):
            start = boundary.start_page_index
            if not 0 <= start < page_count:
                raise ValueError(f"Boundary index out of range: {start}")

            end = page_count - 1
            if idx + 1 < len(plan.boundaries):
                end = plan.boundaries[idx + 1].start_page_index - 1
            if end < start:
                raise ValueError("Split boundaries create an empty or negative range")

            outputs.append(
                SplitOutputSpec(
                    start_page_index=start,
                    end_page_index=end,
                    proposed_filename=SplitService._filename_for_boundary(plan, boundary, idx + 1),
                )
            )

        return outputs

    @staticmethod
    def _normalized_boundaries(page_count: int, starts: Sequence[SplitBoundary | int]) -> tuple[SplitBoundary, ...]:
        normalized: dict[int, SplitBoundary] = {}

        for item in starts:
            boundary = item if isinstance(item, SplitBoundary) else SplitBoundary(start_page_index=item)
            if not 0 <= boundary.start_page_index < page_count:
                raise ValueError(f"Boundary index out of range: {boundary.start_page_index}")
            normalized.setdefault(boundary.start_page_index, boundary)

        normalized.setdefault(0, SplitBoundary(start_page_index=0))
        return tuple(normalized[start] for start in sorted(normalized))

    @staticmethod
    def _filename_for_boundary(plan: SplitPlan, boundary: SplitBoundary, ordinal: int) -> str:
        options = plan.naming_options
        extension = options.extension if options.extension.startswith(".") else f".{options.extension}"
        base = f"{options.prefix}_{ordinal:0{options.zero_pad}d}"

        if options.include_labels and boundary.label:
            sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", boundary.label).strip("_")
            if sanitized:
                base = f"{base}_{sanitized}"

        return f"{base}{extension}"
