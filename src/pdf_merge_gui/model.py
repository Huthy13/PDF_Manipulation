from __future__ import annotations

from typing import Sequence

from .adapters.pypdf_adapter import PdfDocumentSession
from .domain import PageRef, SplitMode, SplitNamingOptions, SplitOutputSpec
from .services.sequence_service import SequenceService
from .services.split_service import SplitService


class MergeModel:
    def __init__(self) -> None:
        self.sequence_service = SequenceService()
        self.split_service = SplitService()
        self.document_session = PdfDocumentSession()

    @property
    def sequence(self) -> list[PageRef]:
        return self.sequence_service.sequence

    def add_pdf(self, path: str) -> None:
        self.sequence_service.extend(self.document_session.load_pdf_pages(path))

    def clear(self) -> None:
        self.sequence_service.clear()
        self.document_session.close()

    def remove(self, indices: Sequence[int]) -> None:
        self.sequence_service.remove(indices)

    def move_up(self, index: int) -> int:
        return self.sequence_service.move_up(index)

    def move_up_many(self, indices: Sequence[int]) -> list[int]:
        return self.sequence_service.move_up_many(indices)

    def move_down(self, index: int) -> int:
        return self.sequence_service.move_down(index)

    def move_down_many(self, indices: Sequence[int]) -> list[int]:
        return self.sequence_service.move_down_many(indices)

    def move_to_many(self, source_indices: Sequence[int], target_index: int) -> list[int]:
        return self.sequence_service.move_to_many(source_indices, target_index)

    def move_to(self, source_index: int, target_index: int) -> int:
        return self.sequence_service.move_to(source_index, target_index)

    def reverse_all(self) -> list[int]:
        return self.sequence_service.reverse_all()

    def reverse_selected(self, indices: Sequence[int]) -> list[int]:
        return self.sequence_service.reverse_selected(indices)

    def rotate_clockwise(self, indices: Sequence[int]) -> list[int]:
        return self.sequence_service.rotate_clockwise(indices)

    def rotate_counterclockwise(self, indices: Sequence[int]) -> list[int]:
        return self.sequence_service.rotate_counterclockwise(indices)

    def write_merged(self, output_path: str) -> None:
        self.document_session.write_merged(self.sequence, output_path)

    def build_split_output_specs(
        self,
        *,
        mode: SplitMode | str,
        page_count: int,
        naming_options: SplitNamingOptions | None = None,
        range_starts: Sequence[int] | None = None,
        every_n: int | None = None,
        bookmark_starts: Sequence[int] | None = None,
        separator_starts: Sequence[int] | None = None,
    ) -> list[SplitOutputSpec]:
        plan = self.split_service.build_plan(
            mode=mode,
            page_count=page_count,
            naming_options=naming_options,
            range_starts=range_starts,
            every_n=every_n,
            bookmark_starts=bookmark_starts,
            separator_starts=separator_starts,
        )
        return self.split_service.emit_output_specs(plan, page_count=page_count)
