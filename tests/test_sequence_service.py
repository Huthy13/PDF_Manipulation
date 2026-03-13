from pdf_merge_gui.domain import PageRef
from pdf_merge_gui.services.sequence_service import SequenceService


def make_page(label: str) -> PageRef:
    return PageRef(source_path=f"/{label}.pdf", page_index=0, display_name=label)


def test_move_up_many_contiguous_block():
    svc = SequenceService()
    svc.extend([make_page("A"), make_page("B"), make_page("C"), make_page("D")])

    new_indices = svc.move_up_many([1, 2])

    assert [p.display_name for p in svc.sequence] == ["B", "C", "A", "D"]
    assert new_indices == [0, 1]


def test_move_down_many_disjoint_blocks():
    svc = SequenceService()
    svc.extend([make_page("A"), make_page("B"), make_page("C"), make_page("D"), make_page("E")])

    new_indices = svc.move_down_many([0, 2])

    assert [p.display_name for p in svc.sequence] == ["B", "A", "D", "C", "E"]
    assert new_indices == [1, 3]


def test_remove_ignores_invalid_indices():
    svc = SequenceService()
    svc.extend([make_page("A"), make_page("B"), make_page("C")])

    svc.remove([-1, 1, 99])

    assert [p.display_name for p in svc.sequence] == ["A", "C"]


def test_move_to_reorders_contiguous_selection_before_target():
    svc = SequenceService()
    svc.extend([make_page("A"), make_page("B"), make_page("C"), make_page("D"), make_page("E")])

    new_indices = svc.move_to([1, 2], 5)

    assert [p.display_name for p in svc.sequence] == ["A", "D", "E", "B", "C"]
    assert new_indices == [3, 4]


def test_move_to_reorders_non_contiguous_selection():
    svc = SequenceService()
    svc.extend([make_page("A"), make_page("B"), make_page("C"), make_page("D"), make_page("E")])

    new_indices = svc.move_to([0, 3], 2)

    assert [p.display_name for p in svc.sequence] == ["B", "A", "D", "C", "E"]
    assert new_indices == [1, 2]
