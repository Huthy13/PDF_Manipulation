from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain import PageRef


class PdfDocumentSession:
    def __init__(self) -> None:
        self._readers: dict[str, Any] = {}

    def _get_reader(self, path: str) -> Any:
        from pypdf import PdfReader

        reader = self._readers.get(path)
        if reader is None:
            reader = PdfReader(path)
            self._readers[path] = reader
        return reader

    def load_pdf_pages(self, path: str) -> list[PageRef]:
        pdf_path = Path(path)
        reader = self._get_reader(str(pdf_path))
        return [
            PageRef(
                source_path=str(pdf_path),
                page_index=idx,
                display_name=f"{pdf_path.name} :: page {idx + 1}",
            )
            for idx in range(len(reader.pages))
        ]

    def write_merged(self, sequence: list[PageRef], output_path: str) -> None:
        from pypdf import PdfWriter

        writer = PdfWriter()
        for page_ref in sequence:
            reader = self._get_reader(page_ref.source_path)
            writer.add_page(reader.pages[page_ref.page_index])

        with open(output_path, "wb") as file_obj:
            writer.write(file_obj)

    def close(self) -> None:
        self._readers.clear()
