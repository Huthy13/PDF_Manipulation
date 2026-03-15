from pathlib import Path

import pytest

pypdf = pytest.importorskip("pypdf")
PdfReader = pypdf.PdfReader
PdfWriter = pypdf.PdfWriter

from pdf_merge_gui.domain import PdfLoadError, PdfMergeWriteError, PdfSourceNotFoundError  # noqa: E402
from pdf_merge_gui.model import MergeModel  # noqa: E402


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


def test_add_pdf_raises_typed_source_not_found_error_with_cause(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"
    model = MergeModel()

    with pytest.raises(PdfSourceNotFoundError) as exc_info:
        model.add_pdf(str(missing))

    assert isinstance(exc_info.value, PdfLoadError)
    assert isinstance(exc_info.value.__cause__, FileNotFoundError)


def test_write_merged_raises_typed_error_with_cause_for_missing_source(tmp_path: Path) -> None:
    pdf1 = tmp_path / "a.pdf"
    write_pdf(pdf1, pages=1)

    model = MergeModel()
    model.add_pdf(str(pdf1))

    model.sequence[0] = model.sequence[0].__class__(
        source_path=str(tmp_path / "gone.pdf"),
        page_index=0,
        display_name="gone.pdf :: page 1",
    )

    with pytest.raises(PdfMergeWriteError) as exc_info:
        model.write_merged(str(tmp_path / "out.pdf"))

    assert isinstance(exc_info.value.__cause__, PdfSourceNotFoundError)
    assert isinstance(exc_info.value.__cause__.__cause__, FileNotFoundError)
