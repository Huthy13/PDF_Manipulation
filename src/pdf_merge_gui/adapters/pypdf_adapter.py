from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain import PageRef, PdfLoadError, PdfMergeWriteError, PdfSourceNotFoundError
from ..services.telemetry import get_telemetry


class PdfDocumentSession:
    def __init__(self) -> None:
        self._readers: dict[str, Any] = {}

    def _get_reader(self, path: str) -> Any:
        from pypdf import PdfReader

        reader = self._readers.get(path)
        if reader is None:
            try:
                reader = PdfReader(path)
            except FileNotFoundError as exc:
                raise PdfSourceNotFoundError(f"PDF source not found: {path}") from exc
            except Exception as exc:
                raise PdfLoadError(f"Failed to load PDF: {path}") from exc
            self._readers[path] = reader
        return reader

    def load_pdf_pages(self, path: str) -> list[PageRef]:
        telemetry = get_telemetry()
        telemetry.increment("load_pdf_pages_calls")
        with telemetry.time_block("load_pdf_pages"):
            pdf_path = Path(path)
            reader = self._get_reader(str(pdf_path))
            pages = [
                PageRef(
                    source_path=str(pdf_path),
                    page_index=idx,
                    display_name=f"{pdf_path.name} :: page {idx + 1}",
                )
                for idx in range(len(reader.pages))
            ]
        for _ in pages:
            telemetry.increment("load_pdf_pages_pages_loaded")
        return pages

    def write_merged(self, sequence: list[PageRef], output_path: str) -> None:
        from pypdf import PdfWriter

        telemetry = get_telemetry()
        writer = PdfWriter()
        with telemetry.time_block("write_merged"):
            try:
                for page_ref in sequence:
                    reader = self._get_reader(page_ref.source_path)
                    page = reader.pages[page_ref.page_index]
                    if page_ref.rotation_degrees:
                        page = page.rotate(page_ref.rotation_degrees)
                    writer.add_page(page)

                with open(output_path, "wb") as file_obj:
                    writer.write(file_obj)
            except Exception as exc:
                raise PdfMergeWriteError(f"Failed to write merged PDF: {output_path}") from exc

        for _ in sequence:
            telemetry.increment("write_merged_pages_exported")

    def close(self) -> None:
        self._readers.clear()
