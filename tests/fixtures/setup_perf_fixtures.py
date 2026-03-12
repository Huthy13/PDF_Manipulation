from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter

FIXTURE_ROOT = Path(__file__).resolve().parent / "perf_docs"

PDF_SPECS = {
    "fixture_small.pdf": 4,
    "fixture_medium.pdf": 12,
    "fixture_large.pdf": 24,
}


def _page_size(index: int) -> tuple[int, int]:
    widths = (612, 720, 840)
    heights = (792, 900, 1080)
    return widths[index % len(widths)], heights[index % len(heights)]


def ensure_perf_fixtures() -> list[Path]:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    for name, page_count in PDF_SPECS.items():
        target = FIXTURE_ROOT / name
        writer = PdfWriter()
        for page_index in range(page_count):
            width, height = _page_size(page_index)
            writer.add_blank_page(width=width, height=height)

        with target.open("wb") as fh:
            writer.write(fh)

        created.append(target)

    return created


if __name__ == "__main__":
    paths = ensure_perf_fixtures()
    for path in paths:
        print(path)
