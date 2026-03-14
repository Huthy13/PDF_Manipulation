from __future__ import annotations

import tkinter as tk
import logging
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional, Sequence

from PIL import Image, ImageTk

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
    FINAL_PREVIEW_LOGICAL_PAGE_HEIGHT = 900
    RESIZE_DEBOUNCE_MS = 120
    FINAL_RESIZE_DEBOUNCE_MS = 180
    FINAL_RESIZE_SETTLE_MS = 240
    FINAL_SCROLL_RENDER_DEBOUNCE_MS = 24
    FINAL_SCROLL_UPGRADE_DEBOUNCE_MS = 220
    RESIZE_NEGLIGIBLE_DELTA_PX = 6
    FINAL_PREVIEW_HIGH_ZOOM_THRESHOLD = 2.0
    FINAL_PREVIEW_HIGH_ZOOM_HARD_CAP = 4
    FINAL_PREVIEW_FRAME_BUDGET_MS = 40
    FINAL_PREVIEW_IDLE_RENDER_DELAY_MS = 12

    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        self.view = PdfMergeView(master)
        self.model = MergeModel()
        self.preview_service = PreviewService(cache_size=120)

        self.preview_zoom = self.DEFAULT_ZOOM
        self._pending_resize_after: Optional[str] = None
        self._pending_final_resize_settle_after: Optional[str] = None
        self._pending_final_scroll_render_after: Optional[str] = None
        self._pending_final_scroll_upgrade_after: Optional[str] = None
        self._pending_final_idle_render_after: Optional[str] = None
        self._last_preview_render_key: Optional[tuple[object, ...]] = None
        self._last_preview_canvas_size: tuple[int, int] = (0, 0)
        self._preview_image_refs: list[ImageTk.PhotoImage] = []
        self._final_preview_pages: list[FinalPreviewPage] = []
        self._final_preview_offsets: list[int] = [0]
        self._final_preview_total_height = 0
        self._final_preview_visible_indices: set[int] = set()
        self._final_preview_anchor_fraction = 0.0
        self._final_preview_syncing_scrollbar = False
        self._final_preview_rendering = False
        self._pending_preview_scroll_restore: Optional[tuple[float, float]] = None
        self._final_preview_prioritize_focus = True
        self._final_preview_deferred_indices: list[int] = []
        self._final_preview_rendered_range: Optional[tuple[int, int]] = None
        self._zoom_interaction_version = 0
        self._zoom_feedback_base_zoom: Optional[float] = None
        self._zoom_feedback_base_range: Optional[tuple[int, int]] = None
        self._zoom_feedback_base_images: list[ImageTk.PhotoImage] = []

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
            self.view.preview_vscroll.configure(command=self._on_final_preview_scrollbar)
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
        pending_upgrade = getattr(self, "_pending_final_scroll_upgrade_after", None)
        if pending_upgrade is not None:
            self.master.after_cancel(pending_upgrade)
            self._pending_final_scroll_upgrade_after = None
        if self._pending_final_idle_render_after is not None:
            self.master.after_cancel(self._pending_final_idle_render_after)
            self._pending_final_idle_render_after = None
        self._final_preview_deferred_indices = []
        self._final_preview_rendered_range = None
        self._zoom_feedback_base_zoom = None
        self._zoom_feedback_base_range = None
        self._zoom_feedback_base_images = []
        self.preview_service.clear()
        self._preview_image_refs = []
        self._final_preview_pages = []
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
        self._final_preview_rendered_range = None
        self._zoom_feedback_base_zoom = None
        self._zoom_feedback_base_range = None
        self._zoom_feedback_base_images = []
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
    ) -> None:
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
        def build() -> list[tk.Widget]:
            preview = tk.Label(self.view.preview_content, image=image, bd=0, highlightthickness=0)
            preview.image = image
            return [preview]

        self._show_preview_widgets(build, reset_scroll=reset_scroll)

    def show_preview_images(self, images: list[ImageTk.PhotoImage], preserve_scroll: bool = False) -> None:
        self._preview_image_refs = list(images)
        self._final_preview_visible_indices = set()
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

    def _resolve_zoom(
        self,
        source_path: str,
        page_index: int,
        quality_tier: str = "focus",
    ) -> tuple[float, ImageTk.PhotoImage]:
        base_zoom = self.preview_zoom
        mode = "single" if self.view.preview_mode.get() == self.view.PREVIEW_SINGLE else "final"
        rendered = self.preview_service.render(source_path, page_index, base_zoom, quality_tier=quality_tier, mode=mode)
        if not self.view.fit_preview.get():
            return base_zoom, rendered

        panel_width, panel_height = self._panel_size()
        width_ratio = panel_width / max(rendered.width(), 1)
        height_ratio = panel_height / max(rendered.height(), 1)
        fit_ratio = min(width_ratio, height_ratio)
        fit_zoom = self._clamp_zoom(base_zoom * fit_ratio)
        if abs(fit_zoom - base_zoom) < 0.01:
            return base_zoom, rendered
        return fit_zoom, self.preview_service.render(source_path, page_index, fit_zoom, quality_tier=quality_tier, mode=mode)

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
        previous_zoom = self.preview_zoom
        next_zoom = self._clamp_zoom(previous_zoom + (-wheel_units * self.ZOOM_STEP))
        if abs(next_zoom - self.preview_zoom) < 0.001:
            return
        self._zoom_interaction_version += 1
        self.preview_zoom = next_zoom
        self._deactivate_fit_preview()
        if (
            self.USE_VIRTUAL_FINAL_PREVIEW
            and self.view.preview_mode.get() == self.view.PREVIEW_FINAL
            and self._apply_immediate_zoom_feedback(previous_zoom, next_zoom)
        ):
            self._final_preview_prioritize_focus = False
            self._schedule_final_resize_settled_render(expected_zoom_version=self._zoom_interaction_version)
            self._last_preview_render_key = None
            return
        self._update_preview_preserving_scroll()

    def _scale_photo_image(self, image: ImageTk.PhotoImage, scale_factor: float) -> ImageTk.PhotoImage:
        pil_image = ImageTk.getimage(image)
        new_size = (
            max(1, int(round(image.width() * scale_factor))),
            max(1, int(round(image.height() * scale_factor))),
        )
        if new_size == (image.width(), image.height()):
            return image
        return ImageTk.PhotoImage(pil_image.resize(new_size, Image.Resampling.LANCZOS))

    def _apply_immediate_zoom_feedback(self, previous_zoom: float, next_zoom: float) -> bool:
        if not self._preview_image_refs:
            return False
        rendered_range = self._final_preview_rendered_range
        if rendered_range is None:
            return False

        if self._zoom_feedback_base_zoom is None or self._zoom_feedback_base_range != rendered_range:
            self._zoom_feedback_base_zoom = previous_zoom
            self._zoom_feedback_base_range = rendered_range
            self._zoom_feedback_base_images = list(self._preview_image_refs)

        base_zoom = self._zoom_feedback_base_zoom
        if base_zoom is None or base_zoom <= 0:
            return False
        scale_factor = next_zoom / base_zoom
        scaled_images = [self._scale_photo_image(image, scale_factor) for image in self._zoom_feedback_base_images]

        rendered_start_idx, rendered_end_idx = rendered_range
        top_spacer = self._final_preview_offsets[rendered_start_idx]
        bottom_spacer = max(self._final_preview_offsets[-1] - self._final_preview_offsets[rendered_end_idx + 1], 0)

        def build() -> list[tk.Widget]:
            widgets: list[tk.Widget] = []
            scaled_top = int(round(top_spacer * scale_factor))
            if scaled_top > 0:
                spacer_top = ttk.Frame(self.view.preview_content, height=scaled_top)
                spacer_top.grid_propagate(False)
                widgets.append(spacer_top)
            for image in scaled_images:
                preview = tk.Label(self.view.preview_content, image=image, bd=0, highlightthickness=0)
                preview.image = image
                widgets.append(preview)
            scaled_bottom = int(round(bottom_spacer * scale_factor))
            if scaled_bottom > 0:
                spacer_bottom = ttk.Frame(self.view.preview_content, height=scaled_bottom)
                spacer_bottom.grid_propagate(False)
                widgets.append(spacer_bottom)
            return widgets

        self._preview_image_refs = scaled_images
        self._show_preview_widgets(build, reset_scroll=False, preserve_scroll=True)
        self._final_preview_syncing_scrollbar = True
        try:
            self.view.preview_canvas.yview_moveto(self._final_preview_anchor_fraction)
        finally:
            self._final_preview_syncing_scrollbar = False
        self._update_zoom_label(effective_zoom=self.preview_zoom)
        return True

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

    def _schedule_final_resize_settled_render(self, expected_zoom_version: Optional[int] = None) -> None:
        if self._pending_final_resize_settle_after is not None:
            self.master.after_cancel(self._pending_final_resize_settle_after)
        self._pending_final_resize_settle_after = self.master.after(
            self.FINAL_RESIZE_SETTLE_MS,
            lambda: self._on_final_resize_settled(expected_zoom_version=expected_zoom_version),
        )

    def _on_final_resize_settled(self, expected_zoom_version: Optional[int] = None) -> None:
        self._pending_final_resize_settle_after = None
        if expected_zoom_version is not None and expected_zoom_version != self._zoom_interaction_version:
            return
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL:
            return
        if not self.USE_VIRTUAL_FINAL_PREVIEW:
            if self.view.fit_preview.get():
                self.update_preview()
            return
        if self._final_preview_rendering:
            self._schedule_final_resize_settled_render(expected_zoom_version=expected_zoom_version)
            return
        self._final_preview_prioritize_focus = True
        if expected_zoom_version is None:
            self._render_virtual_final_preview(preserve_anchor=True)
            return
        self._render_virtual_final_preview(
            preserve_anchor=True,
            expected_zoom_version=expected_zoom_version,
            finalize_zoom=True,
        )

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
            return
        self._final_preview_anchor_fraction = max(0.0, min(1.0, first_fraction))
        self._schedule_final_preview_quality_upgrade()
        if self._pending_final_scroll_render_after is not None:
            return
        self._pending_final_scroll_render_after = self.master.after(
            self.FINAL_SCROLL_RENDER_DEBOUNCE_MS,
            self._render_final_preview_from_scroll,
        )

    def _on_final_preview_scrollbar(self, *args: str) -> None:
        self.view.preview_canvas.yview(*args)
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL:
            return
        if not self.USE_VIRTUAL_FINAL_PREVIEW:
            return
        if self._final_preview_rendering:
            return
        first, _ = self.view.preview_canvas.yview()
        self._final_preview_anchor_fraction = max(0.0, min(1.0, first))
        self._schedule_final_preview_quality_upgrade()
        if self._pending_final_scroll_render_after is not None:
            return
        self._pending_final_scroll_render_after = self.master.after(
            self.FINAL_SCROLL_RENDER_DEBOUNCE_MS,
            self._render_final_preview_from_scroll,
        )

    def _schedule_final_preview_quality_upgrade(self) -> None:
        pending_upgrade = getattr(self, "_pending_final_scroll_upgrade_after", None)
        if pending_upgrade is not None:
            self.master.after_cancel(pending_upgrade)
        self._pending_final_scroll_upgrade_after = self.master.after(
            self.FINAL_SCROLL_UPGRADE_DEBOUNCE_MS,
            self._upgrade_final_preview_quality,
        )

    def _upgrade_final_preview_quality(self) -> None:
        self._pending_final_scroll_upgrade_after = None
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL:
            return
        if not self.USE_VIRTUAL_FINAL_PREVIEW:
            return
        if self._final_preview_rendering:
            self._schedule_final_preview_quality_upgrade()
            return
        self._final_preview_prioritize_focus = True
        self._render_virtual_final_preview(preserve_anchor=True)

    def _render_final_preview_from_scroll(self) -> None:
        self._pending_final_scroll_render_after = None
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL:
            return
        if not self.USE_VIRTUAL_FINAL_PREVIEW:
            return
        if self._final_preview_rendering:
            return
        self._final_preview_prioritize_focus = False
        self._render_virtual_final_preview(preserve_anchor=True)
        self._schedule_final_preview_quality_upgrade()

    def render_preview_image(
        self,
        source_path: str,
        page_index: int,
        quality_tier: str = "focus",
    ) -> Optional[ImageTk.PhotoImage]:
        try:
            effective_zoom, rendered = self._resolve_zoom(source_path, page_index, quality_tier=quality_tier)
            self._update_zoom_label(effective_zoom=effective_zoom)
            return rendered
        except PreviewDependencyUnavailable as exc:
            self._update_zoom_label()
            self.show_preview_text(f"Preview unavailable\n\n{exc}")
            return None
        except PreviewRenderError as exc:
            self._update_zoom_label()
            messagebox.showerror("Preview failed", f"Could not render page preview:\n{exc}")
            self.show_preview_text("Could not render this page.\nThe file may be encrypted or corrupt.")
            return None
        except Exception as exc:
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
            self.preview_service.quantize_zoom(self.preview_zoom),
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
        if sequence == existing:
            return

        previous_heights = {
            (page.source_path, page.page_index): page.estimated_height
            for page in self._final_preview_pages
        }
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
        self._recompute_final_preview_offsets()

    def _recompute_final_preview_offsets(self) -> None:
        if not self._final_preview_pages:
            self._final_preview_offsets = [0]
            self._final_preview_total_height = 0
            return

        page_count = len(self._final_preview_pages)
        available_height = max(
            self.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT - (page_count * self.FINAL_PREVIEW_PAGE_GAP),
            page_count,
        )
        logical_height = min(
            self.FINAL_PREVIEW_LOGICAL_PAGE_HEIGHT,
            max(available_height // page_count, 1),
        )

        offsets = [0]
        running = 0
        for page in self._final_preview_pages:
            page.logical_height = logical_height
            running += page.logical_height + self.FINAL_PREVIEW_PAGE_GAP
            offsets.append(running)
        self._final_preview_offsets = offsets
        self._final_preview_total_height = running

    def _visible_virtual_window(self) -> tuple[int, int]:
        viewport_height = max(self.view.preview_canvas.winfo_height(), 1)
        max_start = max(self._final_preview_total_height - viewport_height, 0)
        virtual_top = int(self._final_preview_anchor_fraction * max_start)
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
        clamped = max(0, min(virtual_top, max_start))
        self._final_preview_anchor_fraction = 0.0 if max_start == 0 else clamped / max_start

    def _center_out_indices(self, start_idx: int, end_idx: int, focus_idx: int) -> list[int]:
        if end_idx < start_idx:
            return []
        indices = [focus_idx]
        left = focus_idx - 1
        right = focus_idx + 1
        while left >= start_idx or right <= end_idx:
            if left >= start_idx:
                indices.append(left)
                left -= 1
            if right <= end_idx:
                indices.append(right)
                right += 1
        return indices

    def _schedule_deferred_virtual_render(self) -> None:
        if not self._final_preview_deferred_indices:
            return
        if self._pending_final_idle_render_after is not None:
            return
        self._pending_final_idle_render_after = self.master.after(
            self.FINAL_PREVIEW_IDLE_RENDER_DELAY_MS,
            self._render_deferred_virtual_final_preview,
        )

    def _render_deferred_virtual_final_preview(self) -> None:
        self._pending_final_idle_render_after = None
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL or not self.USE_VIRTUAL_FINAL_PREVIEW:
            self._final_preview_deferred_indices = []
            return
        if self._final_preview_rendering:
            self._schedule_deferred_virtual_render()
            return
        deferred_indices = list(self._final_preview_deferred_indices)
        self._final_preview_deferred_indices = []
        self._render_virtual_final_preview(preserve_anchor=True, preferred_indices=deferred_indices)

    def _render_virtual_final_preview(
        self,
        preserve_anchor: bool,
        preferred_indices: Optional[Sequence[int]] = None,
        expected_zoom_version: Optional[int] = None,
        finalize_zoom: bool = False,
    ) -> None:
        if self._final_preview_rendering:
            return
        if expected_zoom_version is not None and expected_zoom_version != self._zoom_interaction_version:
            return
        self._final_preview_rendering = True
        try:
            if not self._final_preview_pages:
                self.show_preview_text("Open one or more PDFs to begin.")
                return
            if not preserve_anchor:
                self._set_virtual_anchor(0)

            top, bottom = self._visible_virtual_window()
            start_idx, end_idx = self._visible_page_range(top, bottom)
            if end_idx < start_idx:
                return

            requested_indices = list(range(start_idx, end_idx + 1))
            requested_set = set(requested_indices)
            if preserve_anchor and requested_set == self._final_preview_visible_indices and not preferred_indices:
                self._final_preview_syncing_scrollbar = True
                self.view.preview_canvas.yview_moveto(self._final_preview_anchor_fraction)
                self._final_preview_syncing_scrollbar = False
                return

            focus_idx = (start_idx + end_idx) // 2
            selected_idx = self.selected_index()
            if selected_idx is not None and start_idx <= selected_idx <= end_idx:
                focus_idx = selected_idx

            ordered_indices = self._center_out_indices(start_idx, end_idx, focus_idx)
            if preferred_indices:
                preferred = [idx for idx in preferred_indices if idx in requested_set]
                if preferred:
                    used = set(preferred)
                    ordered_indices = preferred + [idx for idx in ordered_indices if idx not in used]

            hard_cap = len(ordered_indices)
            if self.preview_zoom >= self.FINAL_PREVIEW_HIGH_ZOOM_THRESHOLD:
                hard_cap = min(
                    hard_cap,
                    self.FINAL_PREVIEW_HIGH_ZOOM_HARD_CAP + (self.FINAL_PREVIEW_OVERSCAN_PAGES * 2),
                )

            frame_budget_s = self.FINAL_PREVIEW_FRAME_BUDGET_MS / 1000.0
            pass_started_at = perf_counter()

            images_by_index: dict[int, ImageTk.PhotoImage] = {}
            rendered_order: list[int] = []
            deferred_indices: list[int] = []
            for order, idx in enumerate(ordered_indices):
                if order >= hard_cap:
                    deferred_indices.extend(ordered_indices[order:])
                    break
                if rendered_order and (perf_counter() - pass_started_at) >= frame_budget_s:
                    deferred_indices.extend(ordered_indices[order:])
                    break
                descriptor = self._final_preview_pages[idx]
                is_focus = idx == focus_idx
                quality_tier = "focus" if (self._final_preview_prioritize_focus and is_focus) else "draft"
                rendered = self.render_preview_image(
                    descriptor.source_path,
                    descriptor.page_index,
                    quality_tier=quality_tier,
                )
                if expected_zoom_version is not None and expected_zoom_version != self._zoom_interaction_version:
                    return
                if rendered is None:
                    return
                images_by_index[idx] = rendered
                rendered_order.append(idx)
                if is_focus:
                    measured_height = max(rendered.height(), 1)
                    if measured_height != descriptor.estimated_height:
                        descriptor.estimated_height = measured_height

            if not rendered_order:
                return

            self._recompute_final_preview_offsets()
            rendered_start_idx = min(rendered_order)
            rendered_end_idx = max(rendered_order)

            self._preview_image_refs = [
                images_by_index[idx]
                for idx in range(rendered_start_idx, rendered_end_idx + 1)
                if idx in images_by_index
            ]
            self._final_preview_rendered_range = (rendered_start_idx, rendered_end_idx)
            self._final_preview_visible_indices = set(range(rendered_start_idx, rendered_end_idx + 1))

            top_spacer = self._final_preview_offsets[rendered_start_idx]
            bottom_spacer = max(self._final_preview_offsets[-1] - self._final_preview_offsets[rendered_end_idx + 1], 0)

            def build() -> list[tk.Widget]:
                widgets: list[tk.Widget] = []
                if top_spacer:
                    spacer_top = ttk.Frame(self.view.preview_content, height=top_spacer)
                    spacer_top.grid_propagate(False)
                    widgets.append(spacer_top)
                for idx in range(rendered_start_idx, rendered_end_idx + 1):
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

            self._show_preview_widgets(build, reset_scroll=not preserve_anchor)
            self._final_preview_syncing_scrollbar = True
            try:
                self.view.preview_canvas.yview_moveto(self._final_preview_anchor_fraction)
            finally:
                self._final_preview_syncing_scrollbar = False

            remaining = [idx for idx in deferred_indices if idx not in self._final_preview_visible_indices]
            self._final_preview_deferred_indices = remaining
            if not remaining and self._pending_final_idle_render_after is not None:
                self.master.after_cancel(self._pending_final_idle_render_after)
                self._pending_final_idle_render_after = None
            self._schedule_deferred_virtual_render()

            logger.debug(
                "virtual final preview pass: requested=%d rendered=%d elapsed_ms=%.2f zoom=%.2f cap=%d deferred=%d",
                len(requested_indices),
                len(rendered_order),
                (perf_counter() - pass_started_at) * 1000,
                self.preview_zoom,
                hard_cap,
                len(self._final_preview_deferred_indices),
            )
            if finalize_zoom:
                self._zoom_feedback_base_zoom = None
                self._zoom_feedback_base_range = None
                self._zoom_feedback_base_images = []
        finally:
            self._final_preview_rendering = False

    def update_preview(self) -> None:
        if not self.model.sequence:
            self._last_preview_render_key = None
            self.view.preview_caption.configure(text="No pages loaded")
            self._update_zoom_label()
            self.show_preview_text("Open one or more PDFs to begin.")
            return

        if self.view.preview_mode.get() == self.view.PREVIEW_SINGLE:
            self._final_preview_pages = []
            self._final_preview_visible_indices = set()
            self._final_preview_deferred_indices = []
            self._final_preview_rendered_range = None
            self._zoom_feedback_base_zoom = None
            self._zoom_feedback_base_range = None
            self._zoom_feedback_base_images = []
            if self._pending_final_idle_render_after is not None:
                self.master.after_cancel(self._pending_final_idle_render_after)
                self._pending_final_idle_render_after = None
            idx = self.selected_index()
            if idx is None:
                idx = 0
                self.set_selected_indices([idx])
            page = self.model.sequence[idx]
            self.view.preview_caption.configure(text=f"Single Page ({idx + 1}/{len(self.model.sequence)})")
            preview_key = self._current_preview_key(self.view.PREVIEW_SINGLE, selected_index=idx)
            if preview_key == self._last_preview_render_key:
                return

            rendered = self.render_preview_image(page.source_path, page.page_index)
            if rendered is not None:
                self.show_preview_image(rendered)
                self._last_preview_render_key = preview_key
            return

        self.view.preview_caption.configure(text=f"Final Output ({len(self.model.sequence)} pages)")
        preview_key = self._current_preview_key(self.view.PREVIEW_FINAL)
        if preview_key == self._last_preview_render_key:
            return

        if self.USE_VIRTUAL_FINAL_PREVIEW:
            self._build_final_preview_model()
            self._final_preview_prioritize_focus = True
            self._render_virtual_final_preview(preserve_anchor=True)
        else:
            self._final_preview_pages = []
            self._final_preview_visible_indices = set()
            self._final_preview_rendered_range = None
            self._zoom_feedback_base_zoom = None
            self._zoom_feedback_base_range = None
            self._zoom_feedback_base_images = []
            images: list[ImageTk.PhotoImage] = []
            for page in self.model.sequence:
                rendered = self.render_preview_image(page.source_path, page.page_index)
                if rendered is None:
                    return
                images.append(rendered)
            self.show_preview_images(images, preserve_scroll=False)
        self._last_preview_render_key = preview_key
