from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class PageRef:
    source_path: str
    page_index: int
    display_name: str


def load_pdf_pages(path: str) -> list[PageRef]:
    from pypdf import PdfReader

    pdf_path = Path(path)
    reader = PdfReader(str(pdf_path))
    return [
        PageRef(
            source_path=str(pdf_path),
            page_index=idx,
            display_name=f"{pdf_path.name} :: page {idx + 1}",
        )
        for idx in range(len(reader.pages))
    ]


class MergeModel:
    def __init__(self) -> None:
        self.sequence: list[PageRef] = []

    def add_pdf(self, path: str) -> None:
        self.sequence.extend(load_pdf_pages(path))

    def clear(self) -> None:
        self.sequence.clear()

    def remove(self, indices: Sequence[int]) -> None:
        valid_indices = sorted({idx for idx in indices if 0 <= idx < len(self.sequence)}, reverse=True)
        for idx in valid_indices:
            del self.sequence[idx]

    def move_up(self, index: int) -> int:
        if 0 < index < len(self.sequence):
            self.sequence[index - 1], self.sequence[index] = self.sequence[index], self.sequence[index - 1]
            return index - 1
        return index

    def move_down(self, index: int) -> int:
        if 0 <= index < len(self.sequence) - 1:
            self.sequence[index + 1], self.sequence[index] = self.sequence[index], self.sequence[index + 1]
            return index + 1
        return index

    def write_merged(self, output_path: str) -> None:
        from pypdf import PdfReader, PdfWriter

        writer = PdfWriter()
        readers: dict[str, PdfReader] = {}

        for page_ref in self.sequence:
            reader = readers.get(page_ref.source_path)
            if reader is None:
                reader = PdfReader(page_ref.source_path)
                readers[page_ref.source_path] = reader
            writer.add_page(reader.pages[page_ref.page_index])

        with open(output_path, "wb") as file_obj:
            writer.write(file_obj)
