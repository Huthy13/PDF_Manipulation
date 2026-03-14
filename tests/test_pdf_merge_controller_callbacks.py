from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Generic, TypeVar

import pdf_merge_gui.ui.controller as controller_module
from pdf_merge_gui.ui.controller import PdfMergeController


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
    controller._final_preview_dynamic_overscan_pages = controller.FINAL_PREVIEW_OVERSCAN_PAGES
    controller._final_preview_last_scroll_sample = None
    controller._final_preview_syncing_scrollbar = False
    controller._final_preview_rendering = False
    controller._final_preview_offsets = [0]
    controller._final_preview_total_height = 5_000
    controller._final_preview_visible_indices = set()
    controller._pending_preview_scroll_restore = None
    controller._preview_render_generation = 0
    controller._preview_render_poll_after = None
    controller._preview_render_futures = []
    controller._pending_wheel_zoom_settle_after = None
    controller.preview_executor = SimpleNamespace(shutdown=lambda: None)
    controller.USE_VIRTUAL_FINAL_PREVIEW = True
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


def test_virtual_preview_overscan_expands_with_scroll_velocity(monkeypatch) -> None:
    controller = _build_controller(mode="final")

    now = 100.0

    def fake_monotonic() -> float:
        return now

    monkeypatch.setattr(controller_module, "monotonic", fake_monotonic)

    controller._on_preview_canvas_yscroll("0.10", "0.30")
    assert controller._current_overscan_pages() == controller.FINAL_PREVIEW_OVERSCAN_PAGES

    now += 0.01
    controller._on_preview_canvas_yscroll("0.42", "0.58")

    assert controller._current_overscan_pages() > controller.FINAL_PREVIEW_OVERSCAN_PAGES
    assert controller._current_overscan_pages() <= controller.FINAL_PREVIEW_MAX_OVERSCAN_PAGES


def test_virtual_preview_overscan_returns_to_base_after_idle(monkeypatch) -> None:
    controller = _build_controller(mode="final")

    now = 200.0

    def fake_monotonic() -> float:
        return now

    monkeypatch.setattr(controller_module, "monotonic", fake_monotonic)

    controller._on_preview_canvas_yscroll("0.15", "0.40")
    now += 0.01
    controller._on_preview_canvas_yscroll("0.50", "0.70")
    assert controller._current_overscan_pages() > controller.FINAL_PREVIEW_OVERSCAN_PAGES

    now += (controller.FINAL_PREVIEW_OVERSCAN_IDLE_RESET_MS / 1000) + 0.01
    assert controller._current_overscan_pages() == controller.FINAL_PREVIEW_OVERSCAN_PAGES


def test_virtual_preview_render_submits_visible_and_neighbor_jobs() -> None:
    controller = _build_controller(mode="final")
    controller.preview_zoom = 1.5
    controller._final_preview_pages = [
        controller_module.FinalPreviewPage(source_path=f"doc-{idx}.pdf", page_index=idx, estimated_height=900)
        for idx in range(10)
    ]
    controller._recompute_final_preview_offsets()
    controller._visible_virtual_window = lambda: (0, 1000)
    controller._visible_page_range = lambda top, bottom: (3, 4)
    submitted: list[tuple[int, int, str]] = []

    controller._submit_preview_job = lambda token, page_slot, descriptor, zoom: submitted.append(
        (token, page_slot, descriptor.source_path)
    )
    controller._show_virtual_placeholder_window = lambda start_idx, end_idx: None
    poll_calls: list[int] = []
    controller._schedule_preview_render_poll = lambda delay_ms=12: poll_calls.append(delay_ms)

    controller._render_virtual_final_preview(preserve_anchor=True)

    assert poll_calls == [12]
    submitted_slots = [page_slot for _, page_slot, _ in submitted]
    assert submitted_slots == [3, 4, 2, 5, 1, 6]




def test_virtual_preview_render_mounts_placeholder_window_for_visible_range() -> None:
    controller = _build_controller(mode="final")
    controller.preview_zoom = 1.5
    controller._final_preview_pages = [
        controller_module.FinalPreviewPage(source_path=f"doc-{idx}.pdf", page_index=idx, estimated_height=900)
        for idx in range(8)
    ]
    controller._recompute_final_preview_offsets()
    controller._visible_virtual_window = lambda: (0, 1000)
    controller._visible_page_range = lambda top, bottom: (2, 3)

    placeholder_ranges: list[tuple[int, int]] = []
    controller._show_virtual_placeholder_window = lambda start_idx, end_idx: placeholder_ranges.append((start_idx, end_idx))
    controller._submit_preview_job = lambda *args, **kwargs: None
    controller._schedule_preview_render_poll = lambda delay_ms=12: None

    controller._render_virtual_final_preview(preserve_anchor=True)

    assert placeholder_ranges == [(2, 3)]
def test_apply_render_results_discards_stale_generation() -> None:
    controller = _build_controller(mode="final")
    controller._preview_render_generation = 5
    controller._final_preview_visible_indices = {0}

    built: list[str] = []
    controller._show_preview_widgets = lambda *args, **kwargs: built.append("updated")

    stale_result = controller_module.PreviewRenderResult(
        token=4,
        page_slot=0,
        source_path="x.pdf",
        page_index=0,
        zoom=1.0,
        image=controller_module.Image.new("RGB", (20, 30), color=(255, 255, 255)),
    )

    controller._apply_render_results([stale_result])

    assert built == []


def test_ctrl_wheel_zoom_uses_cached_preview_then_defers_refine() -> None:
    controller = _build_controller(mode="single")
    controller.preview_zoom = 1.5
    controller.model = SimpleNamespace(sequence=[SimpleNamespace(source_path="a.pdf", page_index=0)])
    controller.selected_index = lambda: 0

    shown: list[str] = []
    controller._update_zoom_label = lambda effective_zoom=None: shown.append(f"label:{effective_zoom}")
    controller.show_preview_image = lambda image, reset_scroll=True: shown.append("image")
    controller._update_preview_preserving_scroll = lambda: shown.append("refine")

    class FakeService:
        interaction_zoom_bucket_step_percent = 10

        @staticmethod
        def nearest_cached_photo(*_args, **_kwargs):
            return (1.4, object())

    controller.preview_service = FakeService()
    controller.view.fit_preview = FakeVar(False)

    controller.on_ctrl_wheel_zoom(wheel_units=-1)

    assert "image" in shown
    assert "refine" not in shown
    assert controller._pending_wheel_zoom_settle_after is not None

    settle = controller._pending_wheel_zoom_settle_after
    callback = controller.master.scheduled[settle]
    callback()

    assert "refine" in shown
