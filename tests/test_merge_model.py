from pathlib import Path

import pytest

pypdf = pytest.importorskip("pypdf")
PdfReader = pypdf.PdfReader
PdfWriter = pypdf.PdfWriter

from pdf_merge_gui.model import MergeModel


def write_pdf(path: Path, pages: int) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as f:
        writer.write(f)


def test_write_merged_preserves_selected_order(tmp_path: Path):
    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    out = tmp_path / "merged.pdf"

    write_pdf(pdf1, pages=2)
    write_pdf(pdf2, pages=3)

    model = MergeModel()
    model.add_pdf(str(pdf1))
    model.add_pdf(str(pdf2))

    reordered = [model.sequence[3], model.sequence[0], model.sequence[4]]
    model.sequence.clear()
    model.sequence.extend(reordered)
    model.write_merged(str(out))

    reader = PdfReader(str(out))
    assert len(reader.pages) == 3
