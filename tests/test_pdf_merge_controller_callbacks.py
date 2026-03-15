from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

import pdf_merge_gui.ui.controller as controller_module
import pdf_merge_gui.ui.final_preview_controller as final_preview_module
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
    scrollregion: str = "0 0 1024 768"
    yview_calls: list[float] | None = None

    def winfo_width(self) -> int:
        return self.width

    def winfo_height(self) -> int:
        return self.height


    def cget(self, option: str) -> str:
        if option == "scrollregion":
            return self.scrollregion
        raise KeyError(option)

    def yview_moveto(self, fraction: float) -> None:
        if self.yview_calls is None:
            self.yview_calls = []
        self.yview_calls.append(fraction)


class FakeTk:
    def __init__(self, windowing_system: str = "x11") -> None:
        self.windowing_system = windowing_system

    def call(self, *_args: str) -> str:
        return self.windowing_system


class FakeMaster:
    def __init__(self, *, windowing_system: str = "x11") -> None:
        self._next = 0
        self.after_calls: list[tuple[str, int]] = []
        self.after_cancel_calls: list[str] = []
        self.scheduled: dict[str, object] = {}
        self.tk = FakeTk(windowing_system=windowing_system)

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
        self.preview_content = object()
        self.fit_preview = FakeVar(False)


def _build_controller(
    *,
    mode: str = "final",
    width: int = 1024,
    height: int = 768,
    windowing_system: str = "x11",
) -> PdfMergeController:
    controller = PdfMergeController.__new__(PdfMergeController)
    controller.master = FakeMaster(windowing_system=windowing_system)
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
    controller._final_preview_render_window = None
    controller.final_preview_controller = final_preview_module.FinalPreviewController(controller)
    return controller


def test_regression_final_preview_scroll_loop_does_not_reenter_render() -> None:
    controller = _build_controller(mode="final")
    render_calls: list[float] = []

    def fake_render(*, preserve_anchor: bool) -> None:
        render_calls.append(controller._final_preview_anchor_fraction)
        controller._final_preview_rendering = True
        try:
            controller.final_preview_controller.on_preview_canvas_yscroll("0.73", "0.92")
        finally:
            controller._final_preview_rendering = False

    controller.final_preview_controller.render_virtual_final_preview = fake_render

    for _ in range(20):
        controller.final_preview_controller.on_preview_canvas_yscroll("0.25", "0.60")
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


def test_build_spacer_widgets_chunks_large_heights_without_exceeding_cap(monkeypatch) -> None:
    controller = _build_controller(mode="final")

    class FakeFrame:
        def __init__(self, _parent, *, height: int) -> None:
            self.height = height
            self.propagate: list[bool] = []

        def grid_propagate(self, enabled: bool) -> None:
            self.propagate.append(enabled)

    monkeypatch.setattr(controller_module.ttk, "Frame", FakeFrame)
    monkeypatch.setattr(PdfMergeController, "_spacer_chunk_limit", lambda _self: 10_000)

    spacers = controller._build_spacer_widgets(27_501)

    assert len(spacers) == 3
    assert [spacer.height for spacer in spacers] == [10_000, 10_000, 7_501]
    assert sum(spacer.height for spacer in spacers) == 27_501
    assert max(spacer.height for spacer in spacers) <= 10_000
    assert all(spacer.propagate == [False] for spacer in spacers)


def test_build_spacer_widgets_keeps_single_chunk_below_limit(monkeypatch) -> None:
    controller = _build_controller(mode="final")

    class FakeFrame:
        def __init__(self, _parent, *, height: int) -> None:
            self.height = height

        def grid_propagate(self, _enabled: bool) -> None:
            return None

    monkeypatch.setattr(controller_module.ttk, "Frame", FakeFrame)
    monkeypatch.setattr(PdfMergeController, "_spacer_chunk_limit", lambda _self: 10_000)

    spacers = controller._build_spacer_widgets(9_999)

    assert len(spacers) == 1
    assert spacers[0].height == 9_999


def test_recompute_final_preview_offsets_applies_win32_safe_scroll_cap(monkeypatch) -> None:
    controller = _build_controller(mode="final", windowing_system="win32")
    controller._final_preview_pages = [
        final_preview_module.FinalPreviewPage("a.pdf", 0, estimated_height=20_000),
        final_preview_module.FinalPreviewPage("a.pdf", 1, estimated_height=20_000),
    ]

    monkeypatch.setattr(final_preview_module.sys, "platform", "linux")

    controller.final_preview_controller.recompute_final_preview_offsets()

    assert controller._final_preview_total_height <= controller.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_WIN32


def test_final_preview_safe_scroll_height_uses_default_outside_win32(monkeypatch) -> None:
    controller = _build_controller(mode="final", windowing_system="x11")

    monkeypatch.setattr(final_preview_module.sys, "platform", "linux")

    assert controller._final_preview_safe_scroll_height() == controller.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_DEFAULT


def test_render_virtual_final_preview_clamps_content_height_to_budget_for_many_pages(monkeypatch) -> None:
    controller = _build_controller(mode="final", height=700, windowing_system="win32")
    controller._final_preview_pages = [
        final_preview_module.FinalPreviewPage("big.pdf", idx, estimated_height=2_600)
        for idx in range(120)
    ]
    controller._final_preview_anchor_fraction = 0.65
    controller.final_preview_controller.recompute_final_preview_offsets()

    class FakeImage:
        def __init__(self, height: int) -> None:
            self._height = height

        def height(self) -> int:
            return self._height

    class FakeFrame:
        def __init__(self, _parent, *, height: int) -> None:
            self.height = height

        def grid_propagate(self, _enabled: bool) -> None:
            return None

    class FakeLabel:
        def __init__(self, _parent, *, image, bd: int, highlightthickness: int) -> None:
            assert bd == 0
            assert highlightthickness == 0
            self.height = image.height()
            self.image = image

    measured: dict[str, int] = {}

    def fake_show(widget_builder, reset_scroll: bool = True, preserve_scroll: bool = False) -> None:
        assert preserve_scroll is False
        assert reset_scroll is False
        widgets = widget_builder()
        measured["content_height"] = sum(getattr(widget, "height", 0) for widget in widgets)

    def fake_render(_source_path: str, page_index: int) -> FakeImage:
        return FakeImage(2_200 + (page_index % 3) * 200)

    monkeypatch.setattr(final_preview_module.ttk, "Frame", FakeFrame)
    monkeypatch.setattr(final_preview_module.tk, "Label", FakeLabel)
    monkeypatch.setattr(controller, "_show_preview_widgets", fake_show)
    monkeypatch.setattr(controller, "render_preview_image", fake_render)

    controller.final_preview_controller.render_virtual_final_preview(preserve_anchor=True)

    assert controller._final_preview_visible_indices
    assert measured["content_height"] <= controller.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_WIN32


def test_render_virtual_final_preview_clamps_content_height_for_zoomed_page_heights(monkeypatch) -> None:
    controller = _build_controller(mode="final", height=900, windowing_system="win32")
    controller.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_WIN32 = 18_000
    controller._final_preview_pages = [
        final_preview_module.FinalPreviewPage("zoomed.pdf", idx, estimated_height=1_600)
        for idx in range(80)
    ]
    controller._final_preview_anchor_fraction = 0.3
    controller.final_preview_controller.recompute_final_preview_offsets()

    class FakeImage:
        def __init__(self, height: int) -> None:
            self._height = height

        def height(self) -> int:
            return self._height

    class FakeFrame:
        def __init__(self, _parent, *, height: int) -> None:
            self.height = height

        def grid_propagate(self, _enabled: bool) -> None:
            return None

    class FakeLabel:
        def __init__(self, _parent, *, image, bd: int, highlightthickness: int) -> None:
            assert bd == 0
            assert highlightthickness == 0
            self.height = image.height()
            self.image = image

    measured: dict[str, int] = {}

    def fake_show(widget_builder, reset_scroll: bool = True, preserve_scroll: bool = False) -> None:
        assert preserve_scroll is False
        assert reset_scroll is False
        widgets = widget_builder()
        measured["content_height"] = sum(getattr(widget, "height", 0) for widget in widgets)

    def fake_render(_source_path: str, page_index: int) -> FakeImage:
        zoomed_height = 3_000 if page_index % 7 == 0 else 1_850
        return FakeImage(zoomed_height)

    monkeypatch.setattr(final_preview_module.ttk, "Frame", FakeFrame)
    monkeypatch.setattr(final_preview_module.tk, "Label", FakeLabel)
    monkeypatch.setattr(controller, "_show_preview_widgets", fake_show)
    monkeypatch.setattr(controller, "render_preview_image", fake_render)

    controller.final_preview_controller.render_virtual_final_preview(preserve_anchor=True)

    assert controller._final_preview_visible_indices
    assert measured["content_height"] <= controller.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_WIN32


def test_on_preview_canvas_yscroll_maps_rendered_space_to_logical_anchor_for_clamped_virtualization() -> None:
    controller = _build_controller(mode="final", height=500)
    controller._final_preview_total_height = 100_000
    controller.view.preview_canvas.scrollregion = "0 0 1024 16000"
    controller._final_preview_render_window = final_preview_module.FinalPreviewRenderWindow(
        render_start_idx=30,
        render_end_idx=45,
        logical_start_offset=30_000,
        top_spacer=3_000,
        bottom_spacer=4_000,
        rendered_block_height=9_000,
        content_height=16_000,
    )

    anchors: list[float] = []
    for first in ("0.10", "0.20", "0.40", "0.60"):
        controller.final_preview_controller.on_preview_canvas_yscroll(first, "0.80")
        anchors.append(controller._final_preview_anchor_fraction)

    assert anchors == sorted(anchors)
    assert anchors[-1] - anchors[0] > 0.02


def test_on_preview_canvas_yscroll_clamps_logical_anchor_when_rendered_fraction_hits_extremes() -> None:
    controller = _build_controller(mode="final", height=500)
    controller._final_preview_total_height = 10_500
    controller.view.preview_canvas.scrollregion = "0 0 1024 3000"

    controller._final_preview_render_window = final_preview_module.FinalPreviewRenderWindow(
        render_start_idx=0,
        render_end_idx=5,
        logical_start_offset=100,
        top_spacer=2_400,
        bottom_spacer=100,
        rendered_block_height=500,
        content_height=3_000,
    )
    controller.final_preview_controller.on_preview_canvas_yscroll("0.0", "0.3")
    assert controller._final_preview_anchor_fraction == 0.0

    controller._final_preview_render_window = final_preview_module.FinalPreviewRenderWindow(
        render_start_idx=0,
        render_end_idx=5,
        logical_start_offset=9_900,
        top_spacer=0,
        bottom_spacer=100,
        rendered_block_height=500,
        content_height=3_000,
    )
    controller.final_preview_controller.on_preview_canvas_yscroll("1.0", "1.0")
    assert controller._final_preview_anchor_fraction == 1.0


def test_rendered_scroll_fraction_for_anchor_uses_mapping_and_allows_scrolling_up_from_bottom() -> None:
    controller = _build_controller(mode="final", height=500)
    controller._final_preview_total_height = 100_500
    controller._final_preview_render_window = final_preview_module.FinalPreviewRenderWindow(
        render_start_idx=80,
        render_end_idx=99,
        logical_start_offset=80_000,
        top_spacer=24_000,
        bottom_spacer=500,
        rendered_block_height=5_000,
        content_height=29_500,
    )

    controller._final_preview_anchor_fraction = 1.0
    bottom_fraction = controller.final_preview_controller._rendered_scroll_fraction_for_anchor()
    assert bottom_fraction == 1.0

    controller._final_preview_anchor_fraction = 0.75
    up_fraction = controller.final_preview_controller._rendered_scroll_fraction_for_anchor()
    assert 0.0 <= up_fraction < 1.0
    assert up_fraction < bottom_fraction
