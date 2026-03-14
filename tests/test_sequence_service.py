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


def test_move_to_reorders_item_to_new_position():
    svc = SequenceService()
    svc.extend([make_page("A"), make_page("B"), make_page("C"), make_page("D")])

    new_index = svc.move_to(1, 3)

    assert [p.display_name for p in svc.sequence] == ["A", "C", "B", "D"]
    assert new_index == 2


def test_move_to_end_places_item_last():
    svc = SequenceService()
    svc.extend([make_page("A"), make_page("B"), make_page("C")])

    new_index = svc.move_to(0, 3)

    assert [p.display_name for p in svc.sequence] == ["B", "C", "A"]
    assert new_index == 2


def test_move_to_many_moves_contiguous_selection_as_block():
    svc = SequenceService()
    svc.extend([make_page("A"), make_page("B"), make_page("C"), make_page("D"), make_page("E")])

    new_indices = svc.move_to_many([1, 2], 3)

    assert [p.display_name for p in svc.sequence] == ["A", "D", "E", "B", "C"]
    assert new_indices == [3, 4]


def test_move_to_many_ignores_invalid_indices():
    svc = SequenceService()
    svc.extend([make_page("A"), make_page("B"), make_page("C")])

    new_indices = svc.move_to_many([-1, 1, 99], 0)

    assert [p.display_name for p in svc.sequence] == ["B", "A", "C"]
    assert new_indices == [0]


def test_reverse_selected_flips_only_selected_positions():
    svc = SequenceService()
    svc.extend([make_page("A"), make_page("B"), make_page("C"), make_page("D"), make_page("E")])

    selected = svc.reverse_selected([1, 3, 4])

    assert [p.display_name for p in svc.sequence] == ["A", "E", "C", "D", "B"]
    assert selected == [1, 3, 4]


def test_reverse_all_flips_entire_sequence():
    svc = SequenceService()
    svc.extend([make_page("A"), make_page("B"), make_page("C")])

    selected = svc.reverse_all()

    assert [p.display_name for p in svc.sequence] == ["C", "B", "A"]
    assert selected == [0, 1, 2]


def test_sequence_version_non_mutating_actions_do_not_bump() -> None:
    svc = SequenceService()
    svc.extend([make_page("A"), make_page("B"), make_page("C")])
    initial_version = svc.sequence_version

    assert svc.move_up(0) == 0
    assert svc.move_down(len(svc.sequence) - 1) == len(svc.sequence) - 1
    assert svc.move_to(1, 2) == 1
    assert svc.move_to_many([1], 1) == [1]
    assert svc.reverse_selected([1]) == [1]
    svc.remove([-1, 99])

    assert svc.sequence_version == initial_version


def test_sequence_version_mutating_actions_bump() -> None:
    svc = SequenceService()

    assert svc.sequence_version == 0

    svc.extend([make_page("A")])
    assert svc.sequence_version == 1

    svc.extend([make_page("B")])
    assert svc.sequence_version == 2

    svc.move_up(1)
    assert svc.sequence_version == 3

    svc.move_down(0)
    assert svc.sequence_version == 4

    svc.reverse_selected([0, 1])
    assert svc.sequence_version == 5

    svc.reverse_all()
    assert svc.sequence_version == 6

    svc.remove([0])
    assert svc.sequence_version == 7

    svc.clear()
    assert svc.sequence_version == 8
