from __future__ import annotations

from typing import Sequence

from ..domain import PageRef


class SequenceService:
    def __init__(self) -> None:
        self.sequence: list[PageRef] = []
        self.sequence_version = 0

    def _bump_version(self) -> None:
        self.sequence_version += 1

    def clear(self) -> None:
        if not self.sequence:
            return
        self.sequence.clear()
        self._bump_version()

    def extend(self, pages: Sequence[PageRef]) -> None:
        items = list(pages)
        if not items:
            return
        self.sequence.extend(items)
        self._bump_version()

    def remove(self, indices: Sequence[int]) -> None:
        valid_indices = sorted({idx for idx in indices if 0 <= idx < len(self.sequence)}, reverse=True)
        if not valid_indices:
            return
        for idx in valid_indices:
            del self.sequence[idx]
        self._bump_version()

    def move_up(self, index: int) -> int:
        if 0 < index < len(self.sequence):
            self.sequence[index - 1], self.sequence[index] = self.sequence[index], self.sequence[index - 1]
            self._bump_version()
            return index - 1
        return index

    def move_down(self, index: int) -> int:
        if 0 <= index < len(self.sequence) - 1:
            self.sequence[index + 1], self.sequence[index] = self.sequence[index], self.sequence[index + 1]
            self._bump_version()
            return index + 1
        return index

    def move_up_many(self, indices: Sequence[int]) -> list[int]:
        selected = sorted({idx for idx in indices if 0 <= idx < len(self.sequence)})
        if not selected:
            return []

        moved: set[int] = set()
        block_start = selected[0]
        block_end = selected[0]

        for idx in selected[1:] + [len(self.sequence)]:
            if idx == block_end + 1:
                block_end = idx
                continue

            if block_start > 0:
                head = self.sequence[block_start - 1]
                self.sequence[block_start - 1 : block_end + 1] = self.sequence[block_start : block_end + 1] + [head]
                moved.update(range(block_start, block_end + 1))

            block_start = idx
            block_end = idx

        if moved:
            self._bump_version()
        return [idx - 1 if idx in moved else idx for idx in selected]

    def move_down_many(self, indices: Sequence[int]) -> list[int]:
        selected = sorted({idx for idx in indices if 0 <= idx < len(self.sequence)})
        if not selected:
            return []

        moved: set[int] = set()
        blocks: list[tuple[int, int]] = []
        block_start = selected[0]
        block_end = selected[0]

        for idx in selected[1:] + [len(self.sequence)]:
            if idx == block_end + 1:
                block_end = idx
                continue
            blocks.append((block_start, block_end))
            block_start = idx
            block_end = idx

        for block_start, block_end in reversed(blocks):
            if block_end < len(self.sequence) - 1:
                tail = self.sequence[block_end + 1]
                self.sequence[block_start : block_end + 2] = [tail] + self.sequence[block_start : block_end + 1]
                moved.update(range(block_start, block_end + 1))

        if moved:
            self._bump_version()
        return [idx + 1 if idx in moved else idx for idx in selected]

    def move_to_many(self, source_indices: Sequence[int], target_index: int) -> list[int]:
        selected = sorted({idx for idx in source_indices if 0 <= idx < len(self.sequence)})
        if not selected:
            return []

        selected_set = set(selected)
        moving_pages = [self.sequence[idx] for idx in selected]
        remaining_pages = [page for idx, page in enumerate(self.sequence) if idx not in selected_set]

        insertion_index = max(0, min(target_index, len(remaining_pages)))
        current_positions = list(range(insertion_index, insertion_index + len(moving_pages)))
        if selected == current_positions:
            return current_positions
        self.sequence[:] = remaining_pages[:insertion_index] + moving_pages + remaining_pages[insertion_index:]
        self._bump_version()
        return list(range(insertion_index, insertion_index + len(moving_pages)))

    def move_to(self, source_index: int, target_index: int) -> int:
        if source_index < 0 or source_index >= len(self.sequence):
            return source_index

        target_index = max(0, min(target_index, len(self.sequence)))
        page = self.sequence.pop(source_index)
        if source_index < target_index:
            target_index -= 1
        if source_index == target_index:
            self.sequence.insert(source_index, page)
            return target_index
        self.sequence.insert(target_index, page)
        self._bump_version()
        return target_index

    def reverse_all(self) -> list[int]:
        if not self.sequence:
            return []

        self.sequence.reverse()
        self._bump_version()
        return list(range(len(self.sequence)))

    def reverse_selected(self, indices: Sequence[int]) -> list[int]:
        selected = sorted({idx for idx in indices if 0 <= idx < len(self.sequence)})
        if len(selected) < 2:
            return selected

        selected_pages = [self.sequence[idx] for idx in selected][::-1]
        for idx, page in zip(selected, selected_pages):
            self.sequence[idx] = page
        self._bump_version()
        return selected
