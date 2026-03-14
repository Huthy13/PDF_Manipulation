from __future__ import annotations

import logging
import tkinter as tk
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional, Sequence

from PIL import ImageTk

from ..model import MergeModel
from ..preview import PreviewDependencyUnavailable, PreviewRenderError
from ..services.preview_service import PreviewService
from .view import PdfMergeView


logger = logging.getLogger(__name__)


@dataclass
class FinalPreviewPage:
    source_path: str
    page_index: int
    estimated_height: int
    logical_height: int = 1


class PdfMergeController:
    USE_VIRTUAL_FINAL_PREVIEW = True
    MIN_ZOOM = 0.4
    MAX_ZOOM = 4.0
    ZOOM_STEP = 0.2
    DEFAULT_ZOOM = 1.5
    FINAL_PREVIEW_SAFE_SCROLL_HEIGHT = 900_000
    FINAL_PREVIEW_PAGE_GAP = 12
    FINAL_PREVIEW_OVERSCAN_PAGES = 2
    FINAL_PREVIEW_ESTIMATED_PAGE_HEIGHT = 1300
    RESIZE_DEBOUNCE_MS = 120
    FINAL_RESIZE_DEBOUNCE_MS = 180
    FINAL_RESIZE_SETTLE_MS = 240
    FINAL_SCROLL_RENDER_DEBOUNCE_MS = 24
    FINAL_SCROLL_IDLE_DEBOUNCE_MS = 160
    RESIZE_NEGLIGIBLE_DELTA_PX = 6
    FINAL_PREVIEW_SKIP_WARNING_STREAK = 4
    FINAL_PREVIEW_SKIP_ANCHOR_DELTA_THRESHOLD = 0.015
    FINAL_PREVIEW_HEIGHT_JUMP_WARNING_THRESHOLD = 12_000

    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        self.view = PdfMergeView(master)
        self.model = MergeModel()
        self.preview_service = PreviewService(cache_size=120)

        self.preview_zoom = self.DEFAULT_ZOOM
        self._pending_resize_after: Optional[str] = None
        self._pending_final_resize_settle_after: Optional[str] = None
        self._pending_final_scroll_render_after: Optional[str] = None
        self._pending_final_scroll_idle_after: Optional[str] = None
        self._last_preview_render_key: Optional[tuple[object, ...]] = None
        self._last_preview_canvas_size: tuple[int, int] = (0, 0)
        self._preview_image_refs: list[ImageTk.PhotoImage] = []
        self._final_preview_pages: list[FinalPreviewPage] = []
        self._final_preview_offsets: list[int] = [0]
        self._final_preview_total_height = 0
        self._final_preview_visible_indices: set[int] = set()
        self._final_preview_rendered_indices: set[int] = set()
        self._final_preview_render_signature: Optional[tuple[tuple[int, ...], int, int, int, int]] = None
        self._final_preview_anchor_fraction = 0.0
        self._final_preview_anchor_page_index = 0
        self._final_preview_anchor_intra_fraction = 0.0
        self._final_preview_syncing_scrollbar = False
        self._final_preview_rendering = False
        self._pending_preview_scroll_restore: Optional[tuple[float, float]] = None
        self._final_preview_skip_streak = 0
        self._final_preview_last_skip_anchor: Optional[float] = None
        self._final_preview_last_total_height: Optional[int] = None
        self._final_preview_layout_frozen = False
        self._final_preview_pending_heights: dict[int, int] = {}
        self._final_preview_height_recompute_timestamps: deque[float] = deque()
        self._final_preview_anchor_correction_applied = 0

        self.view.open_handler = self.on_open_pdfs
        self.view.move_up_handler = self.on_move_up
        self.view.move_down_handler = self.on_move_down
        self.view.remove_handler = self.on_remove_selected
        self.view.clear_handler = self.on_clear_all
        self.view.reverse_selected_handler = self.on_reverse_selected
        self.view.reverse_all_handler = self.on_reverse_all
        self.view.merge_handler = self.on_merge_export
        self.view.prev_handler = self.on_prev_preview
        self.view.next_handler = self.on_next_preview
        self.view.selection_handler = self.update_preview
        self.view.preview_mode_handler = self.update_preview
        self.view.zoom_in_handler = self.on_zoom_in
        self.view.zoom_out_handler = self.on_zoom_out
        self.view.zoom_reset_handler = self.on_zoom_reset
        self.view.fit_preview_handler = self.on_toggle_fit_preview
        self.view.ctrl_wheel_zoom_handler = self.on_ctrl_wheel_zoom
        self.view.list_drag_drop_handler = self.on_list_drag_drop
        self.view.list_ctrl_range_handler = self.on_list_ctrl_range
        self.view.bind_handlers()
        self._update_zoom_label()

        self.master.bind("<Delete>", self.on_delete_shortcut)
        self.master.bind("<Control-Up>", self.on_move_up_shortcut)
        self.master.bind("<Control-Down>", self.on_move_down_shortcut)
        self.view.page_list.bind("<Delete>", self.on_delete_shortcut)
        self.view.page_list.bind("<Control-Up>", self.on_move_up_shortcut)
        self.view.page_list.bind("<Control-Down>", self.on_move_down_shortcut)
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)
        self.view.preview_panel.bind("<Configure>", self.on_preview_panel_resize)
        if self.USE_VIRTUAL_FINAL_PREVIEW:
            self.view.preview_canvas.configure(yscrollcommand=self._on_preview_canvas_yscroll)

        self.refresh_list()

    def on_close(self) -> None:
        if self._pending_resize_after is not None:
            self.master.after_cancel(self._pending_resize_after)
            self._pending_resize_after = None
        if self._pending_final_resize_settle_after is not None:
            self.master.after_cancel(self._pending_final_resize_settle_after)
            self._pending_final_resize_settle_after = None
        if self._pending_final_scroll_render_after is not None:
            self.master.after_cancel(self._pending_final_scroll_render_after)
            self._pending_final_scroll_render_after = None
        if self._pending_final_scroll_idle_after is not None:
            self.master.after_cancel(self._pending_final_scroll_idle_after)
            self._pending_final_scroll_idle_after = None
        self._final_preview_pending_heights.clear()
        self.preview_service.clear()
        self._preview_image_refs = []
        self._final_preview_pages = []
        self._final_preview_render_signature = None
        self.model.clear()
        self.master.destroy()

    def selected_indices(self) -> list[int]:
        selected: list[int] = []
        for iid in self.view.page_list.selection():
            try:
                selected.append(int(iid))
            except ValueError:
                continue
        return sorted(set(selected))

    def selected_index(self) -> Optional[int]:
        indices = self.selected_indices()
        return indices[0] if indices else None

    def set_selected_indices(self, indices: Sequence[int]) -> None:
        valid = [idx for idx in sorted(set(indices)) if 0 <= idx < len(self.model.sequence)]
        if not valid:
            self.view.page_list.selection_remove(self.view.page_list.selection())
            return
        iids = [str(idx) for idx in valid]
        self.view.page_list.selection_set(iids)
        self.view.page_list.focus(iids[0])
        self.view.set_list_selection_anchor(valid[0])

    def refresh_list(self, select_index: Optional[int] = None, select_indices: Optional[Sequence[int]] = None) -> None:
        for item in self.view.page_list.get_children():
            self.view.page_list.delete(item)
        for idx, page in enumerate(self.model.sequence):
            filename = Path(page.source_path).name
            self.view.page_list.insert("", tk.END, iid=str(idx), values=(filename, page.page_index + 1))

        if select_indices is not None:
            self.set_selected_indices(select_indices)
        elif select_index is not None and 0 <= select_index < len(self.model.sequence):
            self.set_selected_indices([select_index])
        self.update_preview()

    def on_open_pdfs(self) -> None:
        filepaths: Sequence[str] = filedialog.askopenfilenames(
            title="Select PDF files",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if not filepaths:
            return

        added_any = False
        for filepath in filepaths:
            try:
                self.model.add_pdf(filepath)
            except Exception as exc:
                messagebox.showerror("Could not open PDF", f"Failed to load {Path(filepath).name}:\n{exc}")
                continue
            added_any = True

        if not added_any:
            return

        self.refresh_list(select_index=len(self.model.sequence) - 1)

    def on_move_up(self) -> None:
        indices = self.selected_indices()
        if not indices:
            return
        if len(indices) == 1:
            self.refresh_list(select_index=self.model.move_up(indices[0]))
            return
        self.refresh_list(select_indices=self.model.move_up_many(indices))

    def on_move_down(self) -> None:
        indices = self.selected_indices()
        if not indices:
            return
        if len(indices) == 1:
            self.refresh_list(select_index=self.model.move_down(indices[0]))
            return
        self.refresh_list(select_indices=self.model.move_down_many(indices))

    def on_list_drag_drop(self, source_indices: list[int], preview_index: int) -> None:
        if not self.model.sequence:
            return

        selected = sorted({idx for idx in source_indices if 0 <= idx < len(self.model.sequence)})
        if not selected:
            return

        compact_len = len(self.model.sequence) - len(selected)
        preview_index = max(0, min(preview_index, compact_len))
        moved_indices = self.model.move_to_many(selected, preview_index)
        if moved_indices:
            self.refresh_list(select_indices=moved_indices)

    def on_list_ctrl_range(self, anchor_index: int, clicked_index: int) -> None:
        if not self.model.sequence:
            return

        max_idx = len(self.model.sequence) - 1
        anchor_index = max(0, min(anchor_index, max_idx))
        clicked_index = max(0, min(clicked_index, max_idx))
        start = min(anchor_index, clicked_index)
        end = max(anchor_index, clicked_index)
        self.set_selected_indices(range(start, end + 1))

    def on_move_up_shortcut(self, _event: tk.Event) -> str:
        self.on_move_up()
        return "break"

    def on_move_down_shortcut(self, _event: tk.Event) -> str:
        self.on_move_down()
        return "break"

    def on_remove_selected(self) -> None:
        indices = self.selected_indices()
        if not indices:
            return

        removed_sources = {self.model.sequence[idx].source_path for idx in indices if 0 <= idx < len(self.model.sequence)}
        first_idx = indices[0]
        self.model.remove(indices)
        for source in removed_sources:
            if not any(page.source_path == source for page in self.model.sequence):
                self.preview_service.clear_for_source(source)

        if not self.model.sequence:
            self.refresh_list()
            return

        select_index = min(first_idx, len(self.model.sequence) - 1)
        self.refresh_list(select_index=select_index)

    def on_delete_shortcut(self, _event: tk.Event) -> str:
        self.on_remove_selected()
        return "break"

    def on_clear_all(self) -> None:
        self.model.clear()
        self.preview_service.clear()
        self._preview_image_refs = []
        self._final_preview_pages = []
        self._final_preview_visible_indices = set()
        self._final_preview_rendered_indices = set()
        self._final_preview_render_signature = None
        self._final_preview_pending_heights.clear()
        self._final_preview_pending_heights.clear()
        self._final_preview_layout_frozen = False
        self.refresh_list()

    def on_reverse_selected(self) -> None:
        indices = self.selected_indices()
        if not indices:
            return

        self.refresh_list(select_indices=self.model.reverse_selected(indices))

    def on_reverse_all(self) -> None:
        if not self.model.sequence:
            return

        selected = self.selected_indices()
        if selected:
            max_idx = len(self.model.sequence) - 1
            remapped_selection = [max_idx - idx for idx in selected]
        else:
            remapped_selection = []

        self.model.reverse_all()
        if remapped_selection:
            self.refresh_list(select_indices=remapped_selection)
            return
        self.refresh_list()

    def on_merge_export(self) -> None:
        if not self.model.sequence:
            messagebox.showwarning("Nothing to merge", "Please add at least one PDF page.")
            return

        output_path = filedialog.asksaveasfilename(
            title="Save merged PDF",
            defaultextension=".pdf",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if not output_path:
            return

        try:
            self.model.write_merged(output_path)
        except Exception as exc:
            messagebox.showerror("Merge failed", f"Could not write merged PDF:\n{exc}")
            return

        messagebox.showinfo("Merge complete", f"Merged PDF saved to:\n{output_path}")

    def on_prev_preview(self) -> None:
        if not self.model.sequence:
            return
        if self.view.preview_mode.get() == self.view.PREVIEW_FINAL:
            self.view.preview_canvas.yview_scroll(-1, "pages")
            return

        idx = self.selected_index()
        if idx is None:
            idx = 0
        self.set_selected_indices([max(0, idx - 1)])
        self.update_preview()

    def on_next_preview(self) -> None:
        if not self.model.sequence:
            return
        if self.view.preview_mode.get() == self.view.PREVIEW_FINAL:
            self.view.preview_canvas.yview_scroll(1, "pages")
            return

        idx = self.selected_index()
        if idx is None:
            idx = 0
        self.set_selected_indices([min(len(self.model.sequence) - 1, idx + 1)])
        self.update_preview()

    def _show_preview_widgets(
        self,
        widget_builder: Callable[[], list[tk.Widget]],
        reset_scroll: bool = True,
        preserve_scroll: bool = False,
    ) -> int:
        scroll_to_restore: Optional[tuple[float, float]] = None
        if preserve_scroll:
            scroll_to_restore = self._snapshot_preview_scroll()

        self.view.clear_preview_widgets()
        widgets = widget_builder()
        for row, widget in enumerate(widgets):
            self.view.add_preview_widget(widget, row)
        self.view.refresh_preview_layout()

        if self._pending_preview_scroll_restore is not None:
            scroll_to_restore = self._pending_preview_scroll_restore
            self._pending_preview_scroll_restore = None

        if scroll_to_restore is not None:
            self._restore_preview_scroll(*scroll_to_restore)
        elif reset_scroll:
            self.view.reset_preview_scroll()

        return len(widgets)

    def _snapshot_preview_scroll(self) -> tuple[float, float]:
        return self.view.preview_canvas.xview()[0], self.view.preview_canvas.yview()[0]

    def _clamp_scroll_fraction(self, fraction: float, first: float, last: float) -> float:
        span = max(last - first, 0.0)
        max_offset = max(1.0 - span, 0.0)
        return max(0.0, min(fraction, max_offset))

    def _restore_preview_scroll(self, x: float, y: float) -> None:
        x_first, x_last = self.view.preview_canvas.xview()
        y_first, y_last = self.view.preview_canvas.yview()
        self.view.preview_canvas.xview_moveto(self._clamp_scroll_fraction(x, x_first, x_last))
        self.view.preview_canvas.yview_moveto(self._clamp_scroll_fraction(y, y_first, y_last))

    def _update_preview_preserving_scroll(self) -> None:
        self._pending_preview_scroll_restore = self._snapshot_preview_scroll()
        self.update_preview()
        self._pending_preview_scroll_restore = None

    def show_preview_text(self, text: str) -> None:
        self._preview_image_refs = []
        self._final_preview_rendered_indices = set()
        self._final_preview_render_signature = None
        def build() -> list[tk.Widget]:
            return [
                ttk.Label(
                    self.view.preview_content,
                    text=text,
                    anchor="center",
                    justify="center",
                    padding=24,
                )
            ]

        self._show_preview_widgets(build)

    def show_preview_image(self, image: ImageTk.PhotoImage, reset_scroll: bool = True) -> None:
        self._preview_image_refs = [image]
        self._final_preview_visible_indices = set()
        self._final_preview_rendered_indices = set()
        self._final_preview_render_signature = None
        self._final_preview_pending_heights.clear()
        def build() -> list[tk.Widget]:
            preview = tk.Label(self.view.preview_content, image=image, bd=0, highlightthickness=0)
            preview.image = image
            return [preview]

        self._show_preview_widgets(build, reset_scroll=reset_scroll)

    def show_preview_images(self, images: list[ImageTk.PhotoImage], preserve_scroll: bool = False) -> None:
        self._preview_image_refs = list(images)
        self._final_preview_visible_indices = set()
        self._final_preview_rendered_indices = set()
        self._final_preview_render_signature = None
        self._final_preview_pending_heights.clear()
        def build() -> list[tk.Widget]:
            widgets: list[tk.Widget] = []
            for image in images:
                preview = tk.Label(self.view.preview_content, image=image, bd=0, highlightthickness=0)
                preview.image = image
                widgets.append(preview)
            return widgets

        self._show_preview_widgets(build, preserve_scroll=preserve_scroll)

    def _clamp_zoom(self, zoom: float) -> float:
        return max(self.MIN_ZOOM, min(self.MAX_ZOOM, round(zoom, 2)))

    def _update_zoom_label(self, effective_zoom: Optional[float] = None) -> None:
        zoom_value = effective_zoom if effective_zoom is not None else self.preview_zoom
        suffix = " (fit)" if self.view.fit_preview.get() and effective_zoom is not None else ""
        self.view.zoom_label.configure(text=f"{int(zoom_value * 100)}%{suffix}")

    def _panel_size(self) -> tuple[int, int]:
        width = self.view.preview_canvas.winfo_width() - 8
        height = self.view.preview_canvas.winfo_height() - 8
        return max(width, 1), max(height, 1)

    def _resolve_zoom(self, source_path: str, page_index: int) -> tuple[float, ImageTk.PhotoImage]:
        base_zoom = self.preview_zoom
        fit_mode = bool(self.view.fit_preview.get())
        target_zoom = base_zoom
        used_fallback = False

        if fit_mode:
            panel_width, panel_height = self._panel_size()
            try:
                page_width, page_height = self.preview_service.get_page_dimensions(source_path, page_index)
                width_ratio = panel_width / max(page_width, 1.0)
                height_ratio = panel_height / max(page_height, 1.0)
                fit_zoom = self._clamp_zoom(min(width_ratio, height_ratio))
                if abs(fit_zoom - base_zoom) >= 0.01:
                    target_zoom = fit_zoom
            except PreviewRenderError as exc:
                used_fallback = True
                logger.debug(
                    "Fit zoom metadata unavailable for source=%s page=%s; falling back to one-pass base render sizing: %s",
                    source_path,
                    page_index,
                    exc,
                )

        rendered = self.preview_service.render(source_path, page_index, target_zoom)

        if fit_mode and used_fallback:
            panel_width, panel_height = self._panel_size()
            width_ratio = panel_width / max(rendered.width(), 1)
            height_ratio = panel_height / max(rendered.height(), 1)
            fallback_fit_zoom = self._clamp_zoom(target_zoom * min(width_ratio, height_ratio))
            if abs(fallback_fit_zoom - target_zoom) >= 0.01:
                logger.debug(
                    "Fit zoom fallback retry rendering source=%s page=%s from_zoom=%.2f to_zoom=%.2f",
                    source_path,
                    page_index,
                    target_zoom,
                    fallback_fit_zoom,
                )
                target_zoom = fallback_fit_zoom
                rendered = self.preview_service.render(source_path, page_index, target_zoom)

        return target_zoom, rendered

    def on_zoom_in(self) -> None:
        self.preview_zoom = self._clamp_zoom(self.preview_zoom + self.ZOOM_STEP)
        self._deactivate_fit_preview()
        self._update_preview_preserving_scroll()

    def on_zoom_out(self) -> None:
        self.preview_zoom = self._clamp_zoom(self.preview_zoom - self.ZOOM_STEP)
        self._deactivate_fit_preview()
        self._update_preview_preserving_scroll()

    def on_zoom_reset(self) -> None:
        self.preview_zoom = self.DEFAULT_ZOOM
        self._update_preview_preserving_scroll()

    def on_ctrl_wheel_zoom(self, wheel_units: int) -> None:
        next_zoom = self._clamp_zoom(self.preview_zoom + (-wheel_units * self.ZOOM_STEP))
        if abs(next_zoom - self.preview_zoom) < 0.001:
            return
        self.preview_zoom = next_zoom
        self._deactivate_fit_preview()
        self._update_preview_preserving_scroll()

    def _deactivate_fit_preview(self) -> None:
        if self.view.fit_preview.get():
            self.view.fit_preview.set(False)

    def on_toggle_fit_preview(self) -> None:
        self._update_preview_preserving_scroll()

    def on_preview_panel_resize(self, _event: tk.Event) -> None:
        if self._pending_resize_after is not None:
            self.master.after_cancel(self._pending_resize_after)
        debounce_ms = (
            self.FINAL_RESIZE_DEBOUNCE_MS
            if self.view.preview_mode.get() == self.view.PREVIEW_FINAL
            else self.RESIZE_DEBOUNCE_MS
        )
        self._pending_resize_after = self.master.after(debounce_ms, self._on_resize_debounced)

    def _is_negligible_resize(self, previous: tuple[int, int], current: tuple[int, int]) -> bool:
        return (
            abs(previous[0] - current[0]) <= self.RESIZE_NEGLIGIBLE_DELTA_PX
            and abs(previous[1] - current[1]) <= self.RESIZE_NEGLIGIBLE_DELTA_PX
        )

    def _update_final_preview_window_state(self) -> None:
        top, bottom = self._visible_virtual_window()
        start_idx, end_idx = self._visible_page_range(top, bottom)
        self._final_preview_visible_indices = set(range(start_idx, end_idx + 1)) if end_idx >= start_idx else set()

    def _schedule_final_resize_settled_render(self) -> None:
        if self._pending_final_resize_settle_after is not None:
            self.master.after_cancel(self._pending_final_resize_settle_after)
        self._pending_final_resize_settle_after = self.master.after(
            self.FINAL_RESIZE_SETTLE_MS,
            self._on_final_resize_settled,
        )

    def _on_final_resize_settled(self) -> None:
        self._pending_final_resize_settle_after = None
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL:
            return
        if not self.USE_VIRTUAL_FINAL_PREVIEW:
            if self.view.fit_preview.get():
                self.update_preview()
            return
        if self._final_preview_rendering:
            self._schedule_final_resize_settled_render()
            return
        self._on_final_scroll_idle()

    def _on_resize_debounced(self) -> None:
        self._pending_resize_after = None
        if self.view.preview_mode.get() == self.view.PREVIEW_FINAL:
            if not self.USE_VIRTUAL_FINAL_PREVIEW:
                if self.view.fit_preview.get():
                    self.update_preview()
                return
            current_size = (self.view.preview_canvas.winfo_width(), self.view.preview_canvas.winfo_height())
            previous_size = self._last_preview_canvas_size
            self._last_preview_canvas_size = current_size

            if self._is_negligible_resize(previous_size, current_size):
                return
            if self._final_preview_rendering:
                self._schedule_final_resize_settled_render()
                return

            self._update_final_preview_window_state()
            self._schedule_final_resize_settled_render()
        elif self.view.fit_preview.get():
            self.update_preview()

    def _on_preview_canvas_yscroll(self, first: str, last: str) -> None:
        self.view.preview_vscroll.set(first, last)
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL:
            return
        if not self.USE_VIRTUAL_FINAL_PREVIEW:
            return
        if self._final_preview_syncing_scrollbar or self._final_preview_rendering:
            return
        try:
            first_fraction = float(first)
        except ValueError:
            logger.debug("Preview canvas yscroll callback parse failed raw_first=%r raw_last=%r", first, last)
            return
        clamped_fraction = max(0.0, min(1.0, first_fraction))
        self._final_preview_anchor_fraction = clamped_fraction
        top, _ = self._visible_virtual_window()
        self._capture_virtual_anchor(top)
        self._final_preview_layout_frozen = True
        logger.debug(
            "Preview canvas yscroll callback raw_first=%r raw_last=%r parsed_first=%.6f clamped_anchor=%.6f anchor_clamped=%s anchor_page=%s anchor_intra=%.4f",
            first,
            last,
            first_fraction,
            clamped_fraction,
            first_fraction != clamped_fraction,
            self._final_preview_anchor_page_index,
            self._final_preview_anchor_intra_fraction,
        )
        if self._pending_final_scroll_render_after is None:
            self._pending_final_scroll_render_after = self.master.after(
                self.FINAL_SCROLL_RENDER_DEBOUNCE_MS,
                self._render_final_preview_from_scroll,
            )
        if self._pending_final_scroll_idle_after is not None:
            self.master.after_cancel(self._pending_final_scroll_idle_after)
        self._pending_final_scroll_idle_after = self.master.after(
            self.FINAL_SCROLL_IDLE_DEBOUNCE_MS,
            self._on_final_scroll_idle,
        )

    def _render_final_preview_from_scroll(self) -> None:
        self._pending_final_scroll_render_after = None
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL:
            return
        if not self.USE_VIRTUAL_FINAL_PREVIEW:
            return
        if self._final_preview_rendering:
            return
        self._final_preview_layout_frozen = True
        self._render_virtual_final_preview(preserve_anchor=True)

    def _on_final_scroll_idle(self) -> None:
        self._pending_final_scroll_idle_after = None
        self._final_preview_layout_frozen = False
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL:
            return
        if not self.USE_VIRTUAL_FINAL_PREVIEW:
            return
        if self._final_preview_rendering:
            self._schedule_final_resize_settled_render()
            return

        if self._apply_pending_height_corrections():
            requested_top = self._resolve_anchor_virtual_top_from_absolute()
            self._recompute_final_preview_offsets()
            corrected_top = self._resolve_anchor_virtual_top_from_absolute()
            if corrected_top != requested_top:
                self._final_preview_anchor_correction_applied += 1
                logger.debug(
                    "Anchor correction applied requested_top=%s corrected_top=%s total_applied=%s",
                    requested_top,
                    corrected_top,
                    self._final_preview_anchor_correction_applied,
                )
            self._set_virtual_anchor(corrected_top)

        self._render_virtual_final_preview(preserve_anchor=True)

    def render_preview_image(self, source_path: str, page_index: int) -> Optional[ImageTk.PhotoImage]:
        try:
            effective_zoom, rendered = self._resolve_zoom(source_path, page_index)
            self._update_zoom_label(effective_zoom=effective_zoom)
            return rendered
        except PreviewDependencyUnavailable as exc:
            logger.warning(
                "Preview dependency unavailable for source=%s page=%s zoom=%.2f fit_mode=%s: %s",
                source_path,
                page_index,
                self.preview_zoom,
                bool(self.view.fit_preview.get()),
                exc,
                exc_info=True,
            )
            self._update_zoom_label()
            self.show_preview_text(f"Preview unavailable\n\n{exc}")
            return None
        except PreviewRenderError as exc:
            logger.warning(
                "Preview render failed for source=%s page=%s zoom=%.2f fit_mode=%s: %s",
                source_path,
                page_index,
                self.preview_zoom,
                bool(self.view.fit_preview.get()),
                exc,
                exc_info=True,
            )
            self._update_zoom_label()
            messagebox.showerror("Preview failed", f"Could not render page preview:\n{exc}")
            self.show_preview_text("Could not render this page.\nThe file may be encrypted or corrupt.")
            return None
        except Exception as exc:
            logger.exception(
                "Unexpected preview error for source=%s page=%s zoom=%.2f fit_mode=%s: %s",
                source_path,
                page_index,
                self.preview_zoom,
                bool(self.view.fit_preview.get()),
                exc,
            )
            self._update_zoom_label()
            messagebox.showerror("Preview failed", f"Unexpected preview error:\n{exc}")
            self.show_preview_text("Unexpected error while rendering preview.")
            return None


    def _sequence_signature(self) -> tuple[tuple[str, int], ...]:
        return tuple((page.source_path, page.page_index) for page in self.model.sequence)

    def _current_preview_key(self, mode: str, selected_index: Optional[int] = None) -> tuple[object, ...]:
        key: list[object] = [
            mode,
            self._sequence_signature(),
            round(self.preview_zoom, 2),
            bool(self.view.fit_preview.get()),
        ]
        if self.view.fit_preview.get():
            key.append(self._panel_size())
        if mode == self.view.PREVIEW_SINGLE:
            key.append(selected_index)
        return tuple(key)

    def _build_final_preview_model(self) -> None:
        sequence = [(page.source_path, page.page_index) for page in self.model.sequence]
        existing = [(page.source_path, page.page_index) for page in self._final_preview_pages]
        unique_sources = {source_path for source_path, _ in sequence}
        page_indexes = [page_index for _, page_index in sequence]
        min_page_index = min(page_indexes) if page_indexes else None
        max_page_index = max(page_indexes) if page_indexes else None
        logger.debug(
            "Final preview sequence snapshot sequence_len=%s existing_len=%s first_entries=%s last_entries=%s unique_source_paths=%s min_page_index=%s max_page_index=%s",
            len(sequence),
            len(existing),
            sequence[:3],
            sequence[-3:] if sequence else [],
            len(unique_sources),
            min_page_index,
            max_page_index,
        )

        short_circuited = sequence == existing
        if short_circuited:
            logger.debug(
                "Final preview model rebuild skipped final_preview_pages_len=%s reused_heights=%s default_heights=%s short_circuited=%s",
                len(self._final_preview_pages),
                0,
                0,
                short_circuited,
            )
            return

        self._final_preview_visible_indices = set()
        self._final_preview_rendered_indices = set()
        self._final_preview_render_signature = None
        self._final_preview_pending_heights.clear()

        previous_heights = {
            (page.source_path, page.page_index): page.estimated_height
            for page in self._final_preview_pages
        }
        reused_heights = sum(1 for source_path, page_index in sequence if (source_path, page_index) in previous_heights)
        default_heights = len(sequence) - reused_heights
        self._final_preview_pages = [
            FinalPreviewPage(
                source_path=source_path,
                page_index=page_index,
                estimated_height=previous_heights.get(
                    (source_path, page_index),
                    self.FINAL_PREVIEW_ESTIMATED_PAGE_HEIGHT,
                ),
            )
            for source_path, page_index in sequence
        ]
        logger.debug(
            "Final preview model rebuilt final_preview_pages_len=%s reused_heights=%s default_heights=%s short_circuited=%s",
            len(self._final_preview_pages),
            reused_heights,
            default_heights,
            short_circuited,
        )
        self._recompute_final_preview_offsets()

    def _recompute_final_preview_offsets(self) -> None:
        previous_total_height = self._final_preview_total_height
        if not self._final_preview_pages:
            self._final_preview_offsets = [0]
            self._final_preview_total_height = 0
            self._final_preview_last_total_height = 0
            return

        estimated_total = sum(max(page.estimated_height, 1) for page in self._final_preview_pages)
        available_height = self.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT - (len(self._final_preview_pages) * self.FINAL_PREVIEW_PAGE_GAP)
        scale = 1.0 if estimated_total <= max(available_height, 1) else max(available_height, 1) / estimated_total
        estimated_heights = [page.estimated_height for page in self._final_preview_pages]
        logger.debug(
            "Recomputing final preview offsets estimated_total=%s available_height=%s scale=%.8f page_count=%s",
            estimated_total,
            available_height,
            scale,
            len(self._final_preview_pages),
        )

        offsets = [0]
        running = 0
        for page in self._final_preview_pages:
            page.logical_height = max(int(page.estimated_height * scale), 1)
            running += page.logical_height + self.FINAL_PREVIEW_PAGE_GAP
            offsets.append(running)
        self._final_preview_offsets = offsets
        self._final_preview_total_height = running
        if self._final_preview_last_total_height is not None:
            total_height_delta = abs(self._final_preview_total_height - self._final_preview_last_total_height)
            if total_height_delta > self.FINAL_PREVIEW_HEIGHT_JUMP_WARNING_THRESHOLD:
                logger.warning(
                    "Final preview total height jumped between recomputes previous=%s current=%s delta=%s threshold=%s estimated_total=%s scale=%.8f page_count=%s",
                    self._final_preview_last_total_height,
                    self._final_preview_total_height,
                    total_height_delta,
                    self.FINAL_PREVIEW_HEIGHT_JUMP_WARNING_THRESHOLD,
                    estimated_total,
                    scale,
                    len(self._final_preview_pages),
                )
        self._final_preview_last_total_height = self._final_preview_total_height
        self._record_height_recompute()

        logical_heights = [page.logical_height for page in self._final_preview_pages]
        logger.debug(
            "Final preview offsets stats final_preview_total_height=%s previous_total_height=%s estimated_height_min=%s estimated_height_max=%s logical_height_min=%s logical_height_max=%s recomputes_per_second=%.2f",
            self._final_preview_total_height,
            previous_total_height,
            min(estimated_heights),
            max(estimated_heights),
            min(logical_heights),
            max(logical_heights),
            self._height_recompute_rate(),
        )
        logger.debug(
            "Final preview offsets sample first=%s last=%s offset_count=%s",
            offsets[:5],
            offsets[-5:] if len(offsets) > 5 else offsets,
            len(offsets),
        )

        monotonic = all(current >= previous for previous, current in zip(offsets, offsets[1:]))
        if not monotonic:
            logger.warning(
                "Final preview offsets non-monotonic detected first=%s last=%s",
                offsets[:5],
                offsets[-5:] if len(offsets) > 5 else offsets,
            )
        if offsets[-1] != self._final_preview_total_height:
            logger.warning(
                "Final preview offsets mismatch offset_end=%s final_preview_total_height=%s",
                offsets[-1],
                self._final_preview_total_height,
            )

    def _visible_virtual_window(self) -> tuple[int, int]:
        viewport_height = max(self.view.preview_canvas.winfo_height(), 1)
        max_start = max(self._final_preview_total_height - viewport_height, 0)
        virtual_top = int(self._final_preview_anchor_fraction * max_start)
        logger.debug(
            "Visible virtual window anchor=%.6f viewport_height=%s final_preview_total_height=%s max_start=%s virtual_top=%s",
            self._final_preview_anchor_fraction,
            viewport_height,
            self._final_preview_total_height,
            max_start,
            virtual_top,
        )
        return virtual_top, virtual_top + viewport_height

    def _visible_page_range(self, top: int, bottom: int) -> tuple[int, int]:
        if not self._final_preview_pages:
            return 0, -1
        start = max(bisect_right(self._final_preview_offsets, top) - 1 - self.FINAL_PREVIEW_OVERSCAN_PAGES, 0)
        end = min(
            bisect_right(self._final_preview_offsets, bottom) - 1 + self.FINAL_PREVIEW_OVERSCAN_PAGES,
            len(self._final_preview_pages) - 1,
        )
        return start, end

    def _set_virtual_anchor(self, virtual_top: int) -> None:
        viewport_height = max(self.view.preview_canvas.winfo_height(), 1)
        max_start = max(self._final_preview_total_height - viewport_height, 0)
        requested_virtual_top = virtual_top
        clamped_virtual_top = max(0, min(requested_virtual_top, max_start))
        stored_anchor = 0.0 if max_start == 0 else clamped_virtual_top / max_start
        self._final_preview_anchor_fraction = stored_anchor
        self._capture_virtual_anchor(clamped_virtual_top)
        logger.debug(
            "Set virtual anchor requested_virtual_top=%s clamped_virtual_top=%s anchor_clamped=%s stored_anchor=%.6f anchor_page=%s anchor_intra=%.4f viewport_height=%s final_preview_total_height=%s max_start=%s",
            requested_virtual_top,
            clamped_virtual_top,
            requested_virtual_top != clamped_virtual_top,
            stored_anchor,
            self._final_preview_anchor_page_index,
            self._final_preview_anchor_intra_fraction,
            viewport_height,
            self._final_preview_total_height,
            max_start,
        )

    def _capture_virtual_anchor(self, top: int) -> None:
        if not self._final_preview_pages:
            self._final_preview_anchor_page_index = 0
            self._final_preview_anchor_intra_fraction = 0.0
            return
        clamped_top = max(0, min(top, max(self._final_preview_total_height - 1, 0)))
        page_idx = max(0, min(bisect_right(self._final_preview_offsets, clamped_top) - 1, len(self._final_preview_pages) - 1))
        page_top = self._final_preview_offsets[page_idx]
        page_height = max(self._final_preview_pages[page_idx].logical_height, 1)
        intra_fraction = max(0.0, min((clamped_top - page_top) / page_height, 1.0))
        self._final_preview_anchor_page_index = page_idx
        self._final_preview_anchor_intra_fraction = intra_fraction

    def _resolve_anchor_virtual_top_from_absolute(self) -> int:
        if not self._final_preview_pages:
            return 0
        page_idx = max(0, min(self._final_preview_anchor_page_index, len(self._final_preview_pages) - 1))
        page_top = self._final_preview_offsets[page_idx]
        page_height = max(self._final_preview_pages[page_idx].logical_height, 1)
        return int(page_top + (self._final_preview_anchor_intra_fraction * page_height))

    def _record_height_recompute(self) -> None:
        now = perf_counter()
        self._final_preview_height_recompute_timestamps.append(now)
        while self._final_preview_height_recompute_timestamps and now - self._final_preview_height_recompute_timestamps[0] > 1.0:
            self._final_preview_height_recompute_timestamps.popleft()

    def _height_recompute_rate(self) -> float:
        now = perf_counter()
        while self._final_preview_height_recompute_timestamps and now - self._final_preview_height_recompute_timestamps[0] > 1.0:
            self._final_preview_height_recompute_timestamps.popleft()
        return float(len(self._final_preview_height_recompute_timestamps))

    def _apply_pending_height_corrections(self) -> bool:
        if not self._final_preview_pending_heights:
            return False
        changed = False
        for idx, measured_height in self._final_preview_pending_heights.items():
            if 0 <= idx < len(self._final_preview_pages) and measured_height != self._final_preview_pages[idx].estimated_height:
                self._final_preview_pages[idx].estimated_height = measured_height
                changed = True
        self._final_preview_pending_heights.clear()
        return changed

    def _render_virtual_final_preview(self, preserve_anchor: bool) -> bool:
        if self._final_preview_rendering:
            logger.debug("Skipping virtual final preview render; renderer already active")
            return False
        render_start = perf_counter()
        summary_start_idx: Optional[int] = None
        summary_end_idx: Optional[int] = None
        render_committed = False
        skip_reason = "none"
        self._final_preview_rendering = True
        logger.debug("Rendering virtual final preview preserve_anchor=%s anchor=%.4f pages=%s", preserve_anchor, self._final_preview_anchor_fraction, len(self._final_preview_pages))
        try:
            if not self._final_preview_pages:
                logger.debug("Skipping virtual final preview render; no final preview pages available")
                self.show_preview_text("Open one or more PDFs to begin.")
                skip_reason = "no_pages"
                return False
            if not preserve_anchor:
                self._set_virtual_anchor(0)

            top, bottom = self._visible_virtual_window()
            start_idx, end_idx = self._visible_page_range(top, bottom)
            logger.debug("Virtual preview window top=%s bottom=%s start_idx=%s end_idx=%s", top, bottom, start_idx, end_idx)
            if end_idx < start_idx:
                logger.debug("Skipping virtual preview render; visible page range is empty")
                skip_reason = "empty_visible_range"
                return False
            summary_start_idx = start_idx
            summary_end_idx = end_idx

            requested_indices = set(range(start_idx, end_idx + 1))
            self._final_preview_visible_indices = requested_indices
            requested_indices_sorted = tuple(sorted(requested_indices))
            top_spacer = self._final_preview_offsets[start_idx]
            bottom_spacer = max(self._final_preview_offsets[-1] - self._final_preview_offsets[end_idx + 1], 0)
            canvas_width = max(self.view.preview_canvas.winfo_width(), 1)
            canvas_height = max(self.view.preview_canvas.winfo_height(), 1)
            render_signature = (
                requested_indices_sorted,
                top_spacer,
                bottom_spacer,
                canvas_width,
                canvas_height,
            )
            has_existing_widgets = bool(self.view.preview_content.winfo_children())
            if (
                preserve_anchor
                and requested_indices == self._final_preview_rendered_indices
                and render_signature == self._final_preview_render_signature
                and has_existing_widgets
            ):
                logger.debug(
                    "Skipping virtual render (cache hit, canvas unchanged); requested_indices=%s last_rendered_indices=%s render_signature=%s",
                    list(requested_indices_sorted),
                    sorted(self._final_preview_rendered_indices),
                    render_signature,
                )
                anchor_delta = 0.0
                if self._final_preview_last_skip_anchor is not None:
                    anchor_delta = abs(self._final_preview_anchor_fraction - self._final_preview_last_skip_anchor)
                if anchor_delta > self.FINAL_PREVIEW_SKIP_ANCHOR_DELTA_THRESHOLD:
                    self._final_preview_skip_streak += 1
                else:
                    self._final_preview_skip_streak = 1
                self._final_preview_last_skip_anchor = self._final_preview_anchor_fraction
                if self._final_preview_skip_streak >= self.FINAL_PREVIEW_SKIP_WARNING_STREAK:
                    logger.warning(
                        "Virtual preview repeatedly skipped while anchor changed skip_streak=%s anchor=%.6f anchor_delta=%.6f delta_threshold=%.6f requested_range=%s-%s preserve_anchor=%s",
                        self._final_preview_skip_streak,
                        self._final_preview_anchor_fraction,
                        anchor_delta,
                        self.FINAL_PREVIEW_SKIP_ANCHOR_DELTA_THRESHOLD,
                        start_idx,
                        end_idx,
                        preserve_anchor,
                    )
                self._final_preview_syncing_scrollbar = True
                self.view.preview_canvas.yview_moveto(self._final_preview_anchor_fraction)
                self._final_preview_syncing_scrollbar = False
                skip_reason = "cache_hit"
                return False

            self._final_preview_skip_streak = 0
            self._final_preview_last_skip_anchor = None

            images_by_index: dict[int, ImageTk.PhotoImage] = {}
            height_updates: dict[int, int] = {}
            for idx in range(start_idx, end_idx + 1):
                descriptor = self._final_preview_pages[idx]
                rendered = self.render_preview_image(descriptor.source_path, descriptor.page_index)
                if rendered is None:
                    logger.debug("Virtual preview image render returned None for idx=%s source=%s page=%s", idx, descriptor.source_path, descriptor.page_index)
                    return False
                images_by_index[idx] = rendered
                measured_height = max(rendered.height(), 1)
                if measured_height != descriptor.estimated_height:
                    height_updates[idx] = measured_height

            if height_updates:
                if self._final_preview_layout_frozen:
                    self._final_preview_pending_heights.update(height_updates)
                else:
                    for idx, measured_height in height_updates.items():
                        self._final_preview_pages[idx].estimated_height = measured_height

            if not self._final_preview_layout_frozen and height_updates:
                self._recompute_final_preview_offsets()
                corrected_top = self._resolve_anchor_virtual_top_from_absolute()
                self._set_virtual_anchor(corrected_top)
                top, bottom = self._visible_virtual_window()
                start_idx, end_idx = self._visible_page_range(top, bottom)
                logger.debug("Virtual preview window recomputed top=%s bottom=%s start_idx=%s end_idx=%s", top, bottom, start_idx, end_idx)
            final_requested_indices = set(range(start_idx, end_idx + 1))
            self._final_preview_visible_indices = final_requested_indices

            self._preview_image_refs = [images_by_index[idx] for idx in range(start_idx, end_idx + 1) if idx in images_by_index]

            for idx in range(start_idx, end_idx + 1):
                if idx in images_by_index:
                    continue
                descriptor = self._final_preview_pages[idx]
                rendered = self.render_preview_image(descriptor.source_path, descriptor.page_index)
                if rendered is None:
                    logger.debug("Virtual preview image render returned None after recompute for idx=%s source=%s page=%s", idx, descriptor.source_path, descriptor.page_index)
                    return False
                images_by_index[idx] = rendered

            top_spacer = self._final_preview_offsets[start_idx]
            bottom_spacer = max(self._final_preview_offsets[-1] - self._final_preview_offsets[end_idx + 1], 0)

            def build() -> list[tk.Widget]:
                widgets: list[tk.Widget] = []
                if top_spacer:
                    spacer_top = ttk.Frame(self.view.preview_content, height=top_spacer)
                    spacer_top.grid_propagate(False)
                    widgets.append(spacer_top)
                for idx in range(start_idx, end_idx + 1):
                    image = images_by_index.get(idx)
                    if image is None:
                        continue
                    preview = tk.Label(self.view.preview_content, image=image, bd=0, highlightthickness=0)
                    preview.image = image
                    widgets.append(preview)
                if bottom_spacer:
                    spacer_bottom = ttk.Frame(self.view.preview_content, height=bottom_spacer)
                    spacer_bottom.grid_propagate(False)
                    widgets.append(spacer_bottom)
                return widgets

            logger.debug(
                "Virtual preview canvas update starting start_idx=%s end_idx=%s images_by_index=%s",
                start_idx,
                end_idx,
                len(images_by_index),
            )
            widget_count = self._show_preview_widgets(build, reset_scroll=not preserve_anchor)
            visible_rendered_height = sum(
                image.height()
                for idx, image in images_by_index.items()
                if start_idx <= idx <= end_idx
            )
            preview_canvas_cget = getattr(self.view.preview_canvas, "cget", None)
            scrollregion = preview_canvas_cget("scrollregion") if callable(preview_canvas_cget) else "<unavailable>"
            preview_content = getattr(self.view, "preview_content", None)
            preview_content_reqheight_fn = getattr(preview_content, "winfo_reqheight", None)
            preview_content_reqheight = (
                preview_content_reqheight_fn() if callable(preview_content_reqheight_fn) else "<unavailable>"
            )
            logger.debug(
                "Virtual preview post-widget metrics scrollregion=%s preview_content_reqheight=%s preview_canvas_height=%s top_spacer=%s bottom_spacer=%s visible_rendered_height=%s",
                scrollregion,
                preview_content_reqheight,
                self.view.preview_canvas.winfo_height(),
                top_spacer,
                bottom_spacer,
                visible_rendered_height,
            )
            logger.debug(
                "Virtual preview canvas update finished start_idx=%s end_idx=%s images_by_index=%s widget_count=%s",
                start_idx,
                end_idx,
                len(images_by_index),
                widget_count,
            )
            self._final_preview_rendered_indices = final_requested_indices
            self._final_preview_render_signature = (
                tuple(sorted(final_requested_indices)),
                top_spacer,
                bottom_spacer,
                max(self.view.preview_canvas.winfo_width(), 1),
                max(self.view.preview_canvas.winfo_height(), 1),
            )
            logger.debug("Rendered virtual preview indices=%s top_spacer=%s bottom_spacer=%s", list(range(start_idx, end_idx + 1)), top_spacer, bottom_spacer)
            self._final_preview_syncing_scrollbar = True
            try:
                preview_canvas_yview = getattr(self.view.preview_canvas, "yview", None)
                yview_before = preview_canvas_yview() if callable(preview_canvas_yview) else "<unavailable>"
                logger.debug(
                    "Virtual preview yview before anchor moveto anchor=%.6f yview=%s",
                    self._final_preview_anchor_fraction,
                    yview_before,
                )
                self.view.preview_canvas.yview_moveto(self._final_preview_anchor_fraction)
                yview_after = preview_canvas_yview() if callable(preview_canvas_yview) else "<unavailable>"
                logger.debug(
                    "Virtual preview yview after anchor moveto anchor=%.6f yview=%s",
                    self._final_preview_anchor_fraction,
                    yview_after,
                )
            finally:
                self._final_preview_syncing_scrollbar = False
            render_committed = True
            return True
        finally:
            self._final_preview_rendering = False
            summary_mode = self.view.preview_mode.get()
            summary_range = (
                f"{summary_start_idx}-{summary_end_idx}"
                if summary_start_idx is not None and summary_end_idx is not None
                else "n/a"
            )
            logger.info(
                "Final preview render summary mode=%s range=%s anchor=%.6f anchor_page=%s anchor_intra=%.4f zoom=%.2f fit_mode=%s total_height=%s duration_ms=%.2f committed=%s skip_reason=%s recomputes_per_second=%.2f anchor_corrections=%s pending_height_updates=%s layout_frozen=%s",
                summary_mode,
                summary_range,
                self._final_preview_anchor_fraction,
                self._final_preview_anchor_page_index,
                self._final_preview_anchor_intra_fraction,
                self.preview_zoom,
                self.view.fit_preview.get(),
                self._final_preview_total_height,
                (perf_counter() - render_start) * 1000,
                render_committed,
                skip_reason,
                self._height_recompute_rate(),
                self._final_preview_anchor_correction_applied,
                len(self._final_preview_pending_heights),
                self._final_preview_layout_frozen,
            )
            logger.debug("Virtual final preview render complete")

    def update_preview(self) -> None:
        if not self.model.sequence:
            self._last_preview_render_key = None
            logger.debug("Preview render key reset to None (no pages loaded)")
            self.view.preview_caption.configure(text="No pages loaded")
            self._update_zoom_label()
            self.show_preview_text("Open one or more PDFs to begin.")
            return

        if self.view.preview_mode.get() == self.view.PREVIEW_SINGLE:
            self._final_preview_pages = []
            self._final_preview_visible_indices = set()
            self._final_preview_rendered_indices = set()
            self._final_preview_render_signature = None
            self._final_preview_pending_heights.clear()
            self._final_preview_layout_frozen = False
            idx = self.selected_index()
            if idx is None:
                idx = 0
                self.set_selected_indices([idx])
            page = self.model.sequence[idx]
            self.view.preview_caption.configure(text=f"Single Page ({idx + 1}/{len(self.model.sequence)})")
            preview_key = self._current_preview_key(self.view.PREVIEW_SINGLE, selected_index=idx)
            if preview_key == self._last_preview_render_key:
                logger.debug("Skipping single preview update (cache hit); preview_key=%s", preview_key)
                return

            rendered = self.render_preview_image(page.source_path, page.page_index)
            if rendered is not None:
                self.show_preview_image(rendered)
                self._last_preview_render_key = preview_key
                logger.debug("Preview render key updated after single preview canvas update; preview_key=%s", preview_key)
            return

        self.view.preview_caption.configure(text=f"Final Output ({len(self.model.sequence)} pages)")
        preview_key = self._current_preview_key(self.view.PREVIEW_FINAL)
        if preview_key == self._last_preview_render_key:
            logger.debug("Skipping final preview update (cache hit); preview_key=%s", preview_key)
            return

        if self.USE_VIRTUAL_FINAL_PREVIEW:
            self._build_final_preview_model()
            rendered = self._render_virtual_final_preview(preserve_anchor=True)
            if not rendered:
                logger.debug("Final preview render was not committed; keeping previous preview render key for retry")
                return
        else:
            self._final_preview_pages = []
            self._final_preview_visible_indices = set()
            self._final_preview_rendered_indices = set()
            self._final_preview_render_signature = None
            self._final_preview_pending_heights.clear()
            self._final_preview_layout_frozen = False
            images: list[ImageTk.PhotoImage] = []
            for page in self.model.sequence:
                rendered = self.render_preview_image(page.source_path, page.page_index)
                if rendered is None:
                    return
                images.append(rendered)
            self.show_preview_images(images, preserve_scroll=False)
        self._last_preview_render_key = preview_key
        logger.debug("Preview render key updated after final preview canvas update; preview_key=%s", preview_key)
