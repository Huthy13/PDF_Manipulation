from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from pdf_merge_gui.ui.controller import PdfMergeController
from pdf_merge_gui.ui.controller import FinalPreviewPage


T = TypeVar("T")


class FakeVar(Generic[T]):
    def __init__(self, value: T) -> None:
        self._value = value

    def get(self) -> T:
        return self._value

    def set(self, value: T) -> None:
        self._value = value


class FakeVScroll:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def set(self, first: str, last: str) -> None:
        self.calls.append((first, last))


@dataclass
class FakeCanvas:
    width: int = 1024
    height: int = 768

    def winfo_width(self) -> int:
        return self.width

    def winfo_height(self) -> int:
        return self.height


class FakeMaster:
    def __init__(self) -> None:
        self._next = 0
        self.after_calls: list[tuple[str, int]] = []
        self.after_cancel_calls: list[str] = []
        self.scheduled: dict[str, object] = {}

    def after(self, delay_ms: int, callback):
        self._next += 1
        token = f"after-{self._next}"
        self.after_calls.append((token, delay_ms))
        self.scheduled[token] = callback
        return token

    def after_cancel(self, token: str) -> None:
        self.after_cancel_calls.append(token)
        self.scheduled.pop(token, None)


class FakeView:
    PREVIEW_SINGLE = "single"
    PREVIEW_FINAL = "final"

    def __init__(self, mode: str = PREVIEW_FINAL, width: int = 1024, height: int = 768) -> None:
        self.preview_mode = FakeVar(mode)
        self.preview_vscroll = FakeVScroll()
        self.preview_canvas = FakeCanvas(width=width, height=height)
        self.fit_preview = FakeVar(False)


def _build_controller(*, mode: str = "final", width: int = 1024, height: int = 768) -> PdfMergeController:
    controller = PdfMergeController.__new__(PdfMergeController)
    controller.master = FakeMaster()
    controller.view = FakeView(mode=mode, width=width, height=height)
    controller._pending_resize_after = None
    controller._pending_final_resize_settle_after = None
    controller._pending_final_scroll_render_after = None
    controller._last_preview_canvas_size = (0, 0)
    controller._final_preview_anchor_fraction = 0.0
    controller._final_preview_syncing_scrollbar = False
    controller._final_preview_rendering = False
    controller._final_preview_total_height = 5_000
    controller._final_preview_visible_indices = set()
    return controller


def test_regression_final_preview_scroll_loop_does_not_reenter_render() -> None:
    controller = _build_controller(mode="final")
    render_calls: list[float] = []

    def fake_render(*, preserve_anchor: bool) -> None:
        render_calls.append(controller._final_preview_anchor_fraction)
        controller._final_preview_rendering = True
        try:
            controller._on_preview_canvas_yscroll("0.73", "0.92")
        finally:
            controller._final_preview_rendering = False

    controller._render_virtual_final_preview = fake_render

    for _ in range(20):
        controller._on_preview_canvas_yscroll("0.25", "0.60")
        pending = controller._pending_final_scroll_render_after
        assert pending is not None
        callback = controller.master.scheduled[pending]
        callback()

    assert len(render_calls) == 20
    assert controller._final_preview_anchor_fraction == 0.25
    assert controller.view.preview_vscroll.calls.count(("0.25", "0.60")) == 20
    assert controller.view.preview_vscroll.calls.count(("0.73", "0.92")) == 20


def test_on_preview_panel_resize_debounces_pending_callback() -> None:
    controller = _build_controller(mode="final")

    controller.on_preview_panel_resize(_event=None)
    first_token = controller._pending_resize_after
    controller.on_preview_panel_resize(_event=None)

    assert first_token is not None
    assert controller.master.after_cancel_calls == [first_token]
    assert controller._pending_resize_after != first_token
    assert controller.master.after_calls[-1][1] == controller.FINAL_RESIZE_DEBOUNCE_MS


def test_final_resize_debounced_handler_guards_render_and_settles() -> None:
    controller = _build_controller(mode="final", width=1200, height=900)
    state_updates: list[str] = []
    render_calls: list[bool] = []

    controller._update_final_preview_window_state = lambda: state_updates.append("updated")
    controller._render_virtual_final_preview = lambda *, preserve_anchor: render_calls.append(preserve_anchor)

    controller._final_preview_rendering = True
    controller._on_resize_debounced()

    assert state_updates == []
    assert render_calls == []
    assert controller._pending_final_resize_settle_after is not None

    controller._final_preview_rendering = False
    controller._on_final_resize_settled()

    assert render_calls == [True]


def test_recompute_final_preview_offsets_large_page_count_stays_within_safe_cap() -> None:
    controller = _build_controller(mode="final")
    page_count = 20_000
    controller._final_preview_pages = [
        FinalPreviewPage(source_path="doc.pdf", page_index=idx, estimated_height=1300)
        for idx in range(page_count)
    ]

    controller._recompute_final_preview_offsets()

    offsets = controller._final_preview_offsets
    assert len(offsets) == page_count + 1
    assert offsets[0] == 0
    assert offsets[-1] == controller._final_preview_total_height
    assert controller._final_preview_total_height <= controller.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT
    assert all(offsets[idx] < offsets[idx + 1] for idx in range(page_count))
    assert all(page.logical_height >= 1 for page in controller._final_preview_pages)


def test_recompute_final_preview_offsets_degrades_when_gap_budget_alone_exceeds_cap() -> None:
    controller = _build_controller(mode="final")
    page_count = 90_000
    controller._final_preview_pages = [
        FinalPreviewPage(source_path="doc.pdf", page_index=idx, estimated_height=2)
        for idx in range(page_count)
    ]

    controller._recompute_final_preview_offsets()

    offsets = controller._final_preview_offsets
    heights = [page.logical_height for page in controller._final_preview_pages]
    deltas = [offsets[idx + 1] - offsets[idx] for idx in range(page_count)]

    assert len(offsets) == page_count + 1
    assert all(delta >= 1 for delta in deltas)
    assert all(height >= 1 for height in heights)
    assert all(offsets[idx] < offsets[idx + 1] for idx in range(page_count))
    assert offsets[-1] == controller._final_preview_total_height
    assert controller._final_preview_total_height <= controller.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT
