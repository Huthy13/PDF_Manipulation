import pytest

from pdf_merge_gui.domain import SplitBoundary, SplitMode, SplitNamingOptions
from pdf_merge_gui.model import MergeModel
from pdf_merge_gui.services.split_service import SplitService


def test_parse_mode_accepts_case_and_separator_variants() -> None:
    assert SplitService.parse_mode("range list") == SplitMode.RANGE_LIST
    assert SplitService.parse_mode("every-n") == SplitMode.EVERY_N


def test_compute_boundaries_every_n_is_deterministic() -> None:
    boundaries = SplitService.compute_boundaries(mode="EVERY_N", page_count=10, every_n=3)

    assert [boundary.start_page_index for boundary in boundaries] == [0, 3, 6, 9]


def test_compute_boundaries_bookmark_keeps_first_seen_labels_per_start() -> None:
    boundaries = SplitService.compute_boundaries(
        mode="BOOKMARK",
        page_count=8,
        bookmark_starts=[
            SplitBoundary(start_page_index=4, label="Body"),
            SplitBoundary(start_page_index=4, label="Duplicate"),
            SplitBoundary(start_page_index=2, label="Intro"),
        ],
    )

    assert boundaries == (
        SplitBoundary(start_page_index=0),
        SplitBoundary(start_page_index=2, label="Intro"),
        SplitBoundary(start_page_index=4, label="Body"),
    )


def test_emit_output_specs_uses_deterministic_naming() -> None:
    plan = SplitService.build_plan(
        mode="RANGE_LIST",
        page_count=7,
        range_starts=[0, 3, 5],
        naming_options=SplitNamingOptions(prefix="chunk", zero_pad=2, include_labels=True),
    )
    outputs = SplitService.emit_output_specs(plan, page_count=7)

    assert outputs[0].proposed_filename == "chunk_01.pdf"
    assert outputs[1].proposed_filename == "chunk_02.pdf"
    assert outputs[2].proposed_filename == "chunk_03.pdf"
    assert [(o.start_page_index, o.end_page_index) for o in outputs] == [(0, 2), (3, 4), (5, 6)]


def test_emit_output_specs_includes_sanitized_label_when_enabled() -> None:
    plan = SplitService.build_plan(
        mode=SplitMode.BOOKMARK,
        page_count=6,
        bookmark_starts=[SplitBoundary(start_page_index=2, label="Chapter 1: Intro")],
        naming_options=SplitNamingOptions(prefix="part", zero_pad=3, include_labels=True),
    )

    outputs = SplitService.emit_output_specs(plan, page_count=6)

    assert outputs[1].proposed_filename == "part_002_Chapter_1_Intro.pdf"


def test_split_model_facade_delegates_to_split_service() -> None:
    model = MergeModel()

    outputs = model.build_split_output_specs(mode="EVERY_N", page_count=5, every_n=2)

    assert [(o.start_page_index, o.end_page_index) for o in outputs] == [(0, 1), (2, 3), (4, 4)]


def test_compute_boundaries_rejects_invalid_input() -> None:
    with pytest.raises(ValueError, match="Unsupported split mode"):
        SplitService.parse_mode("something-else")

    with pytest.raises(ValueError, match="every_n must be a positive integer"):
        SplitService.compute_boundaries(mode="EVERY_N", page_count=10, every_n=0)

    with pytest.raises(ValueError, match="Boundary index out of range"):
        SplitService.compute_boundaries(mode="RANGE_LIST", page_count=3, range_starts=[0, 3])
