from __future__ import annotations

from typing import Sequence

from .adapters.pypdf_adapter import PdfDocumentSession
from .domain import PageRef
from .services.sequence_service import SequenceService


class MergeModel:
    def __init__(self) -> None:
        self.sequence_service = SequenceService()
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

    def move_to(self, indices: Sequence[int], target_index: int) -> list[int]:
        return self.sequence_service.move_to(indices, target_index)

    def write_merged(self, output_path: str) -> None:
        self.document_session.write_merged(self.sequence, output_path)
