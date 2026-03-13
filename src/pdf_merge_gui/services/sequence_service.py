from __future__ import annotations

from typing import Sequence

from ..domain import PageRef


class SequenceService:
    def __init__(self) -> None:
        self.sequence: list[PageRef] = []

    def clear(self) -> None:
        self.sequence.clear()

    def extend(self, pages: Sequence[PageRef]) -> None:
        self.sequence.extend(pages)

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

        return [idx + 1 if idx in moved else idx for idx in selected]

    def move_to(self, source_index: int, target_index: int) -> int:
        if source_index < 0 or source_index >= len(self.sequence):
            return source_index

        target_index = max(0, min(target_index, len(self.sequence)))
        page = self.sequence.pop(source_index)
        if source_index < target_index:
            target_index -= 1
        self.sequence.insert(target_index, page)
        return target_index
