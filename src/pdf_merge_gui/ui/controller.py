from __future__ import annotations

import tkinter as tk
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
import sys
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional, Sequence

from PIL import ImageTk

from ..model import MergeModel
from ..preview import PreviewDependencyUnavailable, PreviewRenderError
from ..services.preview_service import PreviewService
from .preview_debug_logger import PreviewDebugLogger
from .view import PdfMergeView


@dataclass
class FinalPreviewPage:
    source_path: str
    page_index: int
    estimated_height: int
    logical_height: int = 1


@dataclass
class FinalPreviewRenderWindow:
    render_start_idx: int
    render_end_idx: int
    logical_start_offset: int
    top_spacer: int
    bottom_spacer: int
    rendered_block_height: int
    content_height: int


class PdfMergeController:
    MIN_ZOOM = 0.4
    MAX_ZOOM = 4.0
    ZOOM_STEP = 0.2
    DEFAULT_ZOOM = 1.5
    FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_DEFAULT = 900_000
    FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_WIN32 = 30_000
    FINAL_PREVIEW_PAGE_GAP = 12
    FINAL_PREVIEW_OVERSCAN_PAGES = 2
    FINAL_PREVIEW_ESTIMATED_PAGE_HEIGHT = 1300
    FINAL_PREVIEW_WIDGET_GRID_PAD_Y = 6
    RESIZE_DEBOUNCE_MS = 120
    FINAL_RESIZE_DEBOUNCE_MS = 180
    FINAL_RESIZE_SETTLE_MS = 240
    FINAL_SCROLL_RENDER_DEBOUNCE_MS = 72
    FINAL_SCROLL_RENDER_ANCHOR_EPSILON = 0.0025
    FINAL_SCROLL_SYNC_EPSILON = 0.001
    RESIZE_NEGLIGIBLE_DELTA_PX = 6

    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        self.view = PdfMergeView(master)
        self.model = MergeModel()
        self.preview_service = PreviewService(cache_size=120)

        self.preview_zoom = self.DEFAULT_ZOOM
        self._pending_resize_after: Optional[str] = None
        self._pending_final_resize_settle_after: Optional[str] = None
        self._pending_final_scroll_render_after: Optional[str] = None
        self._last_preview_render_key: Optional[tuple[object, ...]] = None
        self._last_preview_canvas_size: tuple[int, int] = (0, 0)
        self._preview_image_refs: list[ImageTk.PhotoImage] = []
        self._final_preview_pages: list[FinalPreviewPage] = []
        self._final_preview_offsets: list[int] = [0]
        self._final_preview_total_height = 0
        self._final_preview_visible_indices: set[int] = set()
        self._final_preview_anchor_fraction = 0.0
        self._final_preview_last_scroll_render_anchor = 0.0
        self._final_preview_render_window: Optional[FinalPreviewRenderWindow] = None
        self._final_preview_syncing_scrollbar = False
        self._final_preview_rendering = False

        logging_enabled = PreviewDebugLogger.env_override_enabled(default=False)
        self.preview_debug_logger = PreviewDebugLogger(enabled=logging_enabled)

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
        self.view.preview_debug_logging_handler = self.on_toggle_preview_debug_logging
        self.view.ctrl_wheel_zoom_handler = self.on_ctrl_wheel_zoom
        self.view.list_drag_drop_handler = self.on_list_drag_drop
        self.view.list_ctrl_range_handler = self.on_list_ctrl_range
        self.view.preview_debug_logging.set(logging_enabled)
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
        scroll_x = 0.0
        scroll_y = 0.0
        if preserve_scroll:
            scroll_x = self.view.preview_canvas.xview()[0]
            scroll_y = self.view.preview_canvas.yview()[0]

        self.view.clear_preview_widgets()
        widgets = widget_builder()
        for row, widget in enumerate(widgets):
            self.view.add_preview_widget(widget, row)
        self.view.refresh_preview_layout()

        if preserve_scroll:
            self.view.preview_canvas.xview_moveto(scroll_x)
            self.view.preview_canvas.yview_moveto(scroll_y)
        elif reset_scroll:
            self.view.reset_preview_scroll()

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

    def _resolve_zoom(self, source_path: str, page_index: int) -> tuple[float, ImageTk.PhotoImage]:
        base_zoom = self.preview_zoom
        rendered = self.preview_service.render(source_path, page_index, base_zoom)
        if not self.view.fit_preview.get():
            return base_zoom, rendered

        panel_width, panel_height = self._panel_size()
        width_ratio = panel_width / max(rendered.width(), 1)
        height_ratio = panel_height / max(rendered.height(), 1)
        fit_ratio = min(width_ratio, height_ratio)
        fit_zoom = self._clamp_zoom(base_zoom * fit_ratio)
        if abs(fit_zoom - base_zoom) < 0.01:
            return base_zoom, rendered
        return fit_zoom, self.preview_service.render(source_path, page_index, fit_zoom)

    def on_zoom_in(self) -> None:
        self.preview_zoom = self._clamp_zoom(self.preview_zoom + self.ZOOM_STEP)
        self._deactivate_fit_preview()
        self.update_preview()

    def on_zoom_out(self) -> None:
        self.preview_zoom = self._clamp_zoom(self.preview_zoom - self.ZOOM_STEP)
        self._deactivate_fit_preview()
        self.update_preview()

    def on_zoom_reset(self) -> None:
        self.preview_zoom = self.DEFAULT_ZOOM
        self.update_preview()

    def on_ctrl_wheel_zoom(self, wheel_units: int) -> None:
        next_zoom = self._clamp_zoom(self.preview_zoom + (-wheel_units * self.ZOOM_STEP))
        if abs(next_zoom - self.preview_zoom) < 0.001:
            return
        self.preview_zoom = next_zoom
        self._deactivate_fit_preview()
        self.update_preview()

    def _deactivate_fit_preview(self) -> None:
        if self.view.fit_preview.get():
            self.view.fit_preview.set(False)

    def on_toggle_fit_preview(self) -> None:
        self.update_preview()

    def on_toggle_preview_debug_logging(self) -> None:
        enabled = bool(self.view.preview_debug_logging.get())
        self.preview_debug_logger.set_enabled(enabled)
        self.preview_debug_logger.log(f"preview debug logging enabled={enabled}")

    def _log_preview_debug(self, message: str) -> None:
        logger = getattr(self, "preview_debug_logger", None)
        if logger is None:
            return
        logger.log(message)

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
        if self._final_preview_rendering:
            self._schedule_final_resize_settled_render()
            return
        self._render_virtual_final_preview(preserve_anchor=True)

    def _on_resize_debounced(self) -> None:
        self._pending_resize_after = None
        if self.view.preview_mode.get() == self.view.PREVIEW_FINAL:
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

    def _rendered_scroll_fraction_for_anchor(self) -> float:
        viewport_height = max(self.view.preview_canvas.winfo_height(), 1)
        mapping = self._final_preview_render_window
        if mapping is None:
            return max(0.0, min(1.0, self._final_preview_anchor_fraction))

        max_start = max(self._final_preview_total_height - viewport_height, 0)
        logical_top = self._final_preview_anchor_fraction * max_start
        rendered_top = mapping.top_spacer + (logical_top - mapping.logical_start_offset)
        rendered_max_start = max(mapping.content_height - viewport_height, 0)
        rendered_top = max(0.0, min(rendered_top, float(rendered_max_start)))
        if rendered_max_start == 0:
            return 0.0
        return rendered_top / rendered_max_start

    def _on_preview_canvas_yscroll(self, first: str, last: str) -> None:
        self.view.preview_vscroll.set(first, last)
        self._log_preview_debug(
            f"_on_preview_canvas_yscroll first={first} last={last} anchor_before={self._final_preview_anchor_fraction:.6f} "
            f"syncing={self._final_preview_syncing_scrollbar} rendering={self._final_preview_rendering}"
        )
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL:
            return
        if self._final_preview_syncing_scrollbar or self._final_preview_rendering:
            return
        try:
            first_fraction = float(first)
        except ValueError:
            return
        first_fraction = max(0.0, min(1.0, first_fraction))
        viewport_height = max(self.view.preview_canvas.winfo_height(), 1)
        scrollregion = self.view.preview_canvas.cget("scrollregion")
        rendered_content_height = viewport_height
        if isinstance(scrollregion, str) and scrollregion.strip():
            parts = scrollregion.split()
            if len(parts) == 4:
                try:
                    y1 = float(parts[1])
                    y2 = float(parts[3])
                    rendered_content_height = max(int(y2 - y1), viewport_height)
                except ValueError:
                    rendered_content_height = viewport_height
        mapping = self._final_preview_render_window
        if mapping is not None:
            rendered_content_height = max(rendered_content_height, mapping.content_height)
        rendered_max_start = max(rendered_content_height - viewport_height, 0)
        rendered_top = first_fraction * rendered_max_start

        previous_anchor = self._final_preview_anchor_fraction
        if mapping is None:
            self._final_preview_anchor_fraction = first_fraction
            logical_top = 0.0
        else:
            logical_top = mapping.logical_start_offset + (rendered_top - mapping.top_spacer)
            max_start = max(self._final_preview_total_height - viewport_height, 0)
            logical_top = max(0.0, min(logical_top, float(max_start)))
            self._final_preview_anchor_fraction = 0.0 if max_start == 0 else logical_top / max_start
        self._log_preview_debug(
            f"_on_preview_canvas_yscroll anchor_updated={self._final_preview_anchor_fraction:.6f} "
            f"first_fraction={first_fraction:.6f} rendered_top={rendered_top:.2f} "
            f"rendered_max_start={rendered_max_start} logical_top={logical_top:.2f}"
        )
        last_scroll_render_anchor = getattr(
            self,
            "_final_preview_last_scroll_render_anchor",
            previous_anchor,
        )
        anchor_delta_from_last_render = abs(
            self._final_preview_anchor_fraction - last_scroll_render_anchor
        )
        if mapping is not None and anchor_delta_from_last_render < self.FINAL_SCROLL_RENDER_ANCHOR_EPSILON:
            self._log_preview_debug(
                f"_on_preview_canvas_yscroll skip_scroll_render "
                f"anchor_delta_from_last_render={anchor_delta_from_last_render:.6f} "
                f"threshold={self.FINAL_SCROLL_RENDER_ANCHOR_EPSILON:.6f} "
                f"previous_anchor={previous_anchor:.6f}"
            )
            return
        if self._pending_final_scroll_render_after is not None:
            return
        self._final_preview_last_scroll_render_anchor = self._final_preview_anchor_fraction
        self._pending_final_scroll_render_after = self.master.after(
            self.FINAL_SCROLL_RENDER_DEBOUNCE_MS,
            self._render_final_preview_from_scroll,
        )

    def _render_final_preview_from_scroll(self) -> None:
        self._pending_final_scroll_render_after = None
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL:
            return
        if self._final_preview_rendering:
            return
        self._render_virtual_final_preview(preserve_anchor=True)

    def _sync_canvas_scroll_to_fraction(self, fraction: float) -> bool:
        target_fraction = max(0.0, min(1.0, fraction))
        current_view_getter = getattr(self.view.preview_canvas, "yview", None)
        if callable(current_view_getter):
            current_view = current_view_getter()
            if current_view:
                current_fraction = current_view[0]
                if abs(current_fraction - target_fraction) < self.FINAL_SCROLL_SYNC_EPSILON:
                    return False
        self._final_preview_syncing_scrollbar = True
        try:
            self.view.preview_canvas.yview_moveto(target_fraction)
        finally:
            self._final_preview_syncing_scrollbar = False
        return True

    def render_preview_image(self, source_path: str, page_index: int) -> Optional[ImageTk.PhotoImage]:
        try:
            effective_zoom, rendered = self._resolve_zoom(source_path, page_index)
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

        estimated_total = sum(max(page.estimated_height, 1) for page in self._final_preview_pages)
        safe_scroll_height = self._final_preview_safe_scroll_height()
        available_height = safe_scroll_height - (len(self._final_preview_pages) * self.FINAL_PREVIEW_PAGE_GAP)
        scale = 1.0 if estimated_total <= max(available_height, 1) else max(available_height, 1) / estimated_total

        offsets = [0]
        running = 0
        for page in self._final_preview_pages:
            page.logical_height = max(int(page.estimated_height * scale), 1)
            running += page.logical_height + self.FINAL_PREVIEW_PAGE_GAP
            offsets.append(running)
        self._final_preview_offsets = offsets
        self._final_preview_total_height = running
        max_spacer = 0
        if len(offsets) > 1:
            max_spacer = max(
                self._final_preview_offsets[0],
                max(
                    self._final_preview_offsets[idx + 1] - self._final_preview_offsets[idx]
                    for idx in range(len(self._final_preview_pages))
                ),
            )
        self._log_preview_debug(
            f"_recompute_final_preview_offsets estimated_total={estimated_total} scale={scale:.6f} "
            f"total_height={self._final_preview_total_height} safe_height={safe_scroll_height} "
            f"max_logical_span={max_spacer}"
        )

    def _final_preview_safe_scroll_height(self) -> int:
        if sys.platform == "win32":
            return self.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_WIN32
        try:
            windowing_system = self.master.tk.call("tk", "windowingsystem")
        except Exception:
            return self.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_DEFAULT
        if windowing_system == "win32":
            return self.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_WIN32
        return self.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_DEFAULT

    def _final_preview_safe_canvas_budget(self) -> int:
        is_win32 = self._final_preview_safe_scroll_height() == self.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_WIN32
        configured = (
            self.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_WIN32
            if is_win32
            else self.FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_DEFAULT
        )
        budget = max(int(configured), 1)
        self._log_preview_debug(
            f"_final_preview_safe_canvas_budget is_win32={is_win32} budget={budget}"
        )
        return budget

    def _visible_virtual_window(self) -> tuple[int, int]:
        viewport_height = max(self.view.preview_canvas.winfo_height(), 1)
        max_start = max(self._final_preview_total_height - viewport_height, 0)
        virtual_top = int(self._final_preview_anchor_fraction * max_start)
        window = (virtual_top, virtual_top + viewport_height)
        self._log_preview_debug(
            f"_visible_virtual_window anchor={self._final_preview_anchor_fraction:.6f} viewport_height={viewport_height} "
            f"max_start={max_start} top={window[0]} bottom={window[1]}"
        )
        return window

    def _visible_page_range(self, top: int, bottom: int) -> tuple[int, int]:
        if not self._final_preview_pages:
            self._log_preview_debug(
                f"_visible_page_range top={top} bottom={bottom} pages=0 -> start=0 end=-1"
            )
            return 0, -1
        start = max(bisect_right(self._final_preview_offsets, top) - 1 - self.FINAL_PREVIEW_OVERSCAN_PAGES, 0)
        end = min(
            bisect_right(self._final_preview_offsets, bottom) - 1 + self.FINAL_PREVIEW_OVERSCAN_PAGES,
            len(self._final_preview_pages) - 1,
        )
        self._log_preview_debug(
            f"_visible_page_range top={top} bottom={bottom} pages={len(self._final_preview_pages)} -> start={start} end={end}"
        )
        return start, end

    def _set_virtual_anchor(self, virtual_top: int) -> None:
        viewport_height = max(self.view.preview_canvas.winfo_height(), 1)
        max_start = max(self._final_preview_total_height - viewport_height, 0)
        clamped = max(0, min(virtual_top, max_start))
        self._final_preview_anchor_fraction = 0.0 if max_start == 0 else clamped / max_start

    def _spacer_chunk_limit(self) -> int:
        if sys.platform == "win32":
            return 10_000
        return 50_000

    def _grid_inter_widget_padding(self, widget_count: int) -> int:
        if widget_count <= 1:
            return 0
        return (widget_count - 1) * (self.FINAL_PREVIEW_WIDGET_GRID_PAD_Y * 2)

    def _build_spacer_widgets(self, total_height: int) -> list[tk.Widget]:
        if total_height <= 0:
            return []

        chunk_limit = self._spacer_chunk_limit()
        remaining = total_height
        widgets: list[tk.Widget] = []
        while remaining > 0:
            chunk_height = min(remaining, chunk_limit)
            spacer = ttk.Frame(self.view.preview_content, height=chunk_height)
            spacer.grid_propagate(False)
            widgets.append(spacer)
            remaining -= chunk_height
        return widgets

    def _render_virtual_final_preview(self, preserve_anchor: bool) -> None:
        if self._final_preview_rendering:
            return
        self._final_preview_rendering = True
        try:
            if not self._final_preview_pages:
                self._final_preview_render_window = None
                self.show_preview_text("Open one or more PDFs to begin.")
                return
            if not preserve_anchor:
                self._set_virtual_anchor(0)

            top, bottom = self._visible_virtual_window()
            start_idx, end_idx = self._visible_page_range(top, bottom)
            if end_idx < start_idx:
                return

            requested_indices = set(range(start_idx, end_idx + 1))
            self._log_preview_debug(
                f"_render_virtual_final_preview preserve_anchor={preserve_anchor} start_idx={start_idx} end_idx={end_idx} "
                f"requested_count={len(requested_indices)} viewport={self.view.preview_canvas.winfo_width()}x{self.view.preview_canvas.winfo_height()}"
            )
            if preserve_anchor and requested_indices == self._final_preview_visible_indices:
                rendered_fraction = self._rendered_scroll_fraction_for_anchor()
                self._sync_canvas_scroll_to_fraction(rendered_fraction)
                return

            images_by_index: dict[int, ImageTk.PhotoImage] = {}
            for idx in range(start_idx, end_idx + 1):
                descriptor = self._final_preview_pages[idx]
                rendered = self.render_preview_image(descriptor.source_path, descriptor.page_index)
                if rendered is None:
                    return
                images_by_index[idx] = rendered
                measured_height = max(rendered.height(), 1)
                if measured_height != descriptor.estimated_height:
                    descriptor.estimated_height = measured_height

            self._recompute_final_preview_offsets()
            top, bottom = self._visible_virtual_window()
            start_idx, end_idx = self._visible_page_range(top, bottom)

            safe_canvas_budget = self._final_preview_safe_canvas_budget()
            while start_idx <= end_idx:
                rendered_block_height = (
                    sum(max(images_by_index[idx].height(), 1) for idx in range(start_idx, end_idx + 1) if idx in images_by_index)
                    + self._grid_inter_widget_padding(end_idx - start_idx + 1)
                )
                if rendered_block_height <= safe_canvas_budget:
                    break
                if end_idx - start_idx <= 0:
                    break
                logical_anchor = (top + bottom) // 2
                dist_start = abs(self._final_preview_offsets[start_idx] - logical_anchor)
                dist_end = abs(self._final_preview_offsets[end_idx] - logical_anchor)
                if dist_end >= dist_start:
                    end_idx -= 1
                else:
                    start_idx += 1

            self._preview_image_refs = [images_by_index[idx] for idx in range(start_idx, end_idx + 1) if idx in images_by_index]
            self._final_preview_visible_indices = set(range(start_idx, end_idx + 1))

            for idx in range(start_idx, end_idx + 1):
                if idx in images_by_index:
                    continue
                descriptor = self._final_preview_pages[idx]
                rendered = self.render_preview_image(descriptor.source_path, descriptor.page_index)
                if rendered is None:
                    return
                images_by_index[idx] = rendered

            rendered_block_height = (
                sum(max(images_by_index[idx].height(), 1) for idx in range(start_idx, end_idx + 1))
                + self._grid_inter_widget_padding(end_idx - start_idx + 1)
            )
            top_spacer = self._final_preview_offsets[start_idx]
            bottom_spacer = max(self._final_preview_offsets[-1] - self._final_preview_offsets[end_idx + 1], 0)

            spacer_budget = max(safe_canvas_budget - rendered_block_height, 0)
            clamped = False
            if top_spacer + bottom_spacer > spacer_budget:
                clamped = True
                if top_spacer + bottom_spacer <= 0:
                    top_spacer = 0
                    bottom_spacer = 0
                else:
                    top_ratio = top_spacer / (top_spacer + bottom_spacer)
                    top_spacer = int(spacer_budget * top_ratio)
                    bottom_spacer = spacer_budget - top_spacer

            content_height = top_spacer + rendered_block_height + bottom_spacer
            logical_start_offset = self._final_preview_offsets[start_idx]
            if clamped:
                self._log_preview_debug(
                    f"_render_virtual_final_preview clamped start_idx={start_idx} end_idx={end_idx} "
                    f"rendered_block_height={rendered_block_height} top_spacer={top_spacer} "
                    f"bottom_spacer={bottom_spacer} content_height={content_height}"
                )

            self._final_preview_render_window = FinalPreviewRenderWindow(
                render_start_idx=start_idx,
                render_end_idx=end_idx,
                logical_start_offset=logical_start_offset,
                top_spacer=top_spacer,
                bottom_spacer=bottom_spacer,
                rendered_block_height=rendered_block_height,
                content_height=content_height,
            )

            top_chunks = len(self._build_spacer_widgets(top_spacer))
            bottom_chunks = len(self._build_spacer_widgets(bottom_spacer))
            self._log_preview_debug(
                f"_render_virtual_final_preview spacer_stats top={top_spacer} bottom={bottom_spacer} "
                f"top_chunks={top_chunks} bottom_chunks={bottom_chunks} rendered_block_height={rendered_block_height} "
                f"safe_canvas_budget={safe_canvas_budget} content_height={content_height}"
            )


            def build() -> list[tk.Widget]:
                widgets: list[tk.Widget] = []
                widgets.extend(self._build_spacer_widgets(top_spacer))
                for idx in range(start_idx, end_idx + 1):
                    image = images_by_index.get(idx)
                    if image is None:
                        continue
                    preview = tk.Label(self.view.preview_content, image=image, bd=0, highlightthickness=0)
                    preview.image = image
                    widgets.append(preview)
                widgets.extend(self._build_spacer_widgets(bottom_spacer))
                return widgets

            self._show_preview_widgets(build, reset_scroll=not preserve_anchor)
            rendered_fraction = self._rendered_scroll_fraction_for_anchor()
            self._sync_canvas_scroll_to_fraction(rendered_fraction)
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

        self._build_final_preview_model()
        self._render_virtual_final_preview(preserve_anchor=True)
        self._last_preview_render_key = preview_key
