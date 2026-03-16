from __future__ import annotations

from dataclasses import dataclass

from pdf_merge_gui.ui.view import PdfMergeView


class FakeTk:
    def __init__(self, windowing_system: str = "x11") -> None:
        self.windowing_system = windowing_system

    def call(self, *_args: str) -> str:
        return self.windowing_system


class FakeMaster:
    def __init__(self, windowing_system: str = "x11") -> None:
        self.tk = FakeTk(windowing_system)


class FakeCanvas:
    def __init__(self, *, width: int = 100, height: int = 100, scrollregion: str = "0 0 500 500") -> None:
        self._width = width
        self._height = height
        self._scrollregion = scrollregion
        self.y_fraction = 0.0
        self.x_fraction = 0.0
        self.y_scroll_calls: list[tuple[int, str]] = []
        self.x_scroll_calls: list[tuple[int, str]] = []

    def cget(self, option: str) -> str:
        if option != "scrollregion":
            raise KeyError(option)
        return self._scrollregion

    def winfo_height(self) -> int:
        return self._height

    def winfo_width(self) -> int:
        return self._width

    def yview(self) -> tuple[float, float]:
        return (self.y_fraction, min(self.y_fraction + 0.1, 1.0))

    def xview(self) -> tuple[float, float]:
        return (self.x_fraction, min(self.x_fraction + 0.1, 1.0))

    def yview_moveto(self, fraction: float) -> None:
        self.y_fraction = fraction

    def xview_moveto(self, fraction: float) -> None:
        self.x_fraction = fraction

    def yview_scroll(self, units: int, mode: str) -> None:
        self.y_scroll_calls.append((units, mode))

    def xview_scroll(self, units: int, mode: str) -> None:
        self.x_scroll_calls.append((units, mode))


@dataclass
class FakeEvent:
    delta: int = 0
    state: int = 0
    num: int | None = None


def _build_view(*, feature_enabled: bool = True, capable: bool = True) -> PdfMergeView:
    view = PdfMergeView.__new__(PdfMergeView)
    view.master = FakeMaster(windowing_system="x11")
    view.preview_canvas = FakeCanvas()
    view._wheel_accum_y = 0.0
    view._wheel_accum_x = 0.0
    view._wheel_accum_zoom = 0.0
    view._wheel_pixel_accum_y = 0.0
    view._wheel_pixel_accum_x = 0.0
    view._wheel_pixels_per_notch = 40.0
    view._wheel_delta_deadzone_px = 0.35
    view._wheel_delta_damping = 0.85
    view._wheel_pixel_scroll_feature_enabled = feature_enabled
    view._wheel_pixel_scroll_capable = capable
    view.ctrl_wheel_zoom_handler = None
    return view


def test_preview_mousewheel_uses_pixel_scroll_when_supported() -> None:
    view = _build_view(feature_enabled=True, capable=True)

    result = view.on_preview_mousewheel(FakeEvent(delta=-120))

    assert result == "break"
    assert view.preview_canvas.y_fraction > 0.0
    assert view.preview_canvas.y_scroll_calls == []


def test_preview_mousewheel_falls_back_to_units_when_pixel_scroll_disabled() -> None:
    view = _build_view(feature_enabled=False, capable=True)

    result = view.on_preview_mousewheel(FakeEvent(delta=-120))

    assert result == "break"
    assert view.preview_canvas.y_scroll_calls == [(1, "units")]


def test_preview_mousewheel_keeps_linux_button_wheel_fallback() -> None:
    view = _build_view(feature_enabled=True, capable=True)

    result = view.on_preview_mousewheel(FakeEvent(num=4))

    assert result == "break"
    assert view.preview_canvas.y_scroll_calls == [(-1, "units")]


def test_preview_mousewheel_deadzone_blocks_micro_jitter() -> None:
    view = _build_view(feature_enabled=True, capable=True)

    result = view.on_preview_mousewheel(FakeEvent(delta=-1))

    assert result == "break"
    assert view.preview_canvas.y_fraction == 0.0
    assert view.preview_canvas.y_scroll_calls == []


def test_preview_shift_mousewheel_uses_pixel_scroll_horizontally() -> None:
    view = _build_view(feature_enabled=True, capable=True)

    result = view.on_preview_shift_mousewheel(FakeEvent(delta=-120))

    assert result == "break"
    assert view.preview_canvas.x_fraction > 0.0
    assert view.preview_canvas.x_scroll_calls == []


def test_detect_pixel_scroll_capability_from_windowing_system() -> None:
    view = PdfMergeView.__new__(PdfMergeView)
    view.preview_canvas = FakeCanvas()
    view.master = FakeMaster(windowing_system="x11")

    assert view._detect_pixel_scroll_capability() is True

    view.master = FakeMaster(windowing_system="wayland")
    assert view._detect_pixel_scroll_capability() is False
