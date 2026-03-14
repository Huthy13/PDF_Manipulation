from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from pdf_merge_gui.ui.controller import FinalPreviewPage, PdfMergeController


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


class FakePipeline:
    def __init__(self) -> None:
        self.generations: list[int] = []

    def set_active_generation(self, generation_id: int) -> None:
        self.generations.append(generation_id)

    def stop(self) -> None:
        return


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
    controller._final_preview_anchor_page_index = 0
    controller._final_preview_anchor_offset_px_within_page = 0
    controller._final_preview_syncing_scrollbar = False
    controller._final_preview_rendering = False
    controller._final_preview_total_height = 5_060
    controller._final_preview_offsets = [0]
    controller._final_preview_visible_indices = set()
    controller._final_preview_pages = [
        FinalPreviewPage(source_path="a.pdf", page_index=idx, estimated_height=1000, logical_height=1000)
        for idx in range(5)
    ]
    controller._final_preview_offsets = [0, 1012, 2024, 3036, 4048, 5060]
    controller._final_preview_generation = 0
    controller._final_preview_pending_indices = set()
    controller._final_preview_images_by_index = {}
    controller._final_preview_active_range = (0, -1)
    controller._final_preview_pipeline = FakePipeline()
    return controller


def test_regression_final_preview_scroll_loop_debounces_and_cancels_prior_dispatch() -> None:
    controller = _build_controller(mode="final")
    render_calls: list[bool] = []
    controller._request_final_preview_render = lambda *, preserve_anchor, refresh_generation: render_calls.append(
        preserve_anchor and refresh_generation
    )

    for _ in range(20):
        controller._on_preview_canvas_yscroll("0.25", "0.60")

    assert controller._virtual_top_from_anchor() == int(round(0.25 * controller._max_viewport_start()))
    assert len(controller.master.after_cancel_calls) == 19
    assert controller.view.preview_vscroll.calls.count(("0.25", "0.60")) == 20

    pending = controller._pending_final_scroll_render_after
    assert pending is not None
    callback = controller.master.scheduled[pending]
    callback()
    assert render_calls == [True]


def test_on_preview_panel_resize_debounces_pending_callback() -> None:
    controller = _build_controller(mode="final")

    controller.on_preview_panel_resize(_event=None)
    first_token = controller._pending_resize_after
    controller.on_preview_panel_resize(_event=None)

    assert first_token is not None
    assert controller.master.after_cancel_calls == [first_token]
    assert controller._pending_resize_after != first_token
    assert controller.master.after_calls[-1][1] == controller.FINAL_RESIZE_DEBOUNCE_MS


def test_final_resize_debounced_handler_schedules_settled_render() -> None:
    controller = _build_controller(mode="final", width=1200, height=900)
    state_updates: list[str] = []
    render_calls: list[bool] = []

    controller._update_final_preview_window_state = lambda: state_updates.append("updated")
    controller._request_final_preview_render = lambda *, preserve_anchor, refresh_generation: render_calls.append(
        preserve_anchor and refresh_generation
    )

    controller._on_resize_debounced()

    assert state_updates == ["updated"]
    assert render_calls == []
    assert controller._pending_final_resize_settle_after is not None

    controller._on_final_resize_settled()

    assert render_calls == [True]
