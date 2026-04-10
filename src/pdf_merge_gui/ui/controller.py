from __future__ import annotations

import os
import tkinter as tk
import time
from pathlib import Path
import sys
from tkinter import filedialog, messagebox
from typing import Callable, Optional, Sequence

from PIL import ImageTk

from ..domain import PdfLoadError, PdfMergeWriteError, PdfSourceNotFoundError
from ..model import MergeModel
from ..preview import PreviewDependencyUnavailable, PreviewRenderError
from ..services.preview_service import PreviewService
from ..services.telemetry import get_telemetry
from .final_preview_controller import (
    FinalPreviewController,
    FinalPreviewPage,
    FinalPreviewRenderWindow,
)
from .render_worker import RenderRequest, RenderWorker, build_photo_image
from .view import PdfMergeView, PreviewWidgetSpec


class PdfMergeController:
    MIN_ZOOM = 0.4
    MAX_ZOOM = 4.0
    ZOOM_STEP = 0.2
    DEFAULT_ZOOM = 1.5
    FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_DEFAULT = 900_000
    FINAL_PREVIEW_SAFE_SCROLL_HEIGHT_WIN32 = 30_000
    FINAL_PREVIEW_PAGE_GAP = 12
    FINAL_PREVIEW_OVERSCAN_PAGES = 2
    FINAL_PREVIEW_MIN_OVERSCAN_PAGES = 1
    FINAL_PREVIEW_MAX_OVERSCAN_PAGES = 9
    FINAL_PREVIEW_OVERSCAN_SLOW_MAX_VELOCITY_PX_S = 300.0
    FINAL_PREVIEW_OVERSCAN_MEDIUM_MAX_VELOCITY_PX_S = 1200.0
    FINAL_PREVIEW_OVERSCAN_MEDIUM_PAGES = 4
    FINAL_PREVIEW_OVERSCAN_FAST_PAGES = 7
    FINAL_PREVIEW_DYNAMIC_OVERSCAN_ENV = "PDF_MERGE_GUI_DYNAMIC_OVERSCAN"
    FINAL_SCROLL_DYNAMIC_DEBOUNCE_ENV = "PDF_MERGE_GUI_DYNAMIC_SCROLL_DEBOUNCE"
    FINAL_PREVIEW_ESTIMATED_PAGE_HEIGHT = 1300
    FINAL_PREVIEW_WIDGET_GRID_PAD_Y = 6
    RESIZE_DEBOUNCE_MS = 120
    FINAL_RESIZE_DEBOUNCE_MS = 180
    FINAL_RESIZE_SETTLE_MS = 240
    FINAL_SCROLL_RENDER_DEBOUNCE_MS = 72
    FINAL_SCROLL_RENDER_DEBOUNCE_SLOW_MS = 24
    FINAL_SCROLL_RENDER_DEBOUNCE_MEDIUM_MS = 48
    FINAL_SCROLL_RENDER_DEBOUNCE_FAST_MS = 96
    FINAL_SCROLL_RENDER_MAX_UPDATE_INTERVAL_MS = 120
    FINAL_SCROLL_VELOCITY_EMA_ALPHA = 0.35
    ZOOM_RENDER_DEBOUNCE_MS = 60
    FINAL_SCROLL_RENDER_ANCHOR_EPSILON = 0.0025
    FINAL_SCROLL_SYNC_EPSILON = 0.001
    FINAL_SCROLL_SYNC_CALLBACK_SUPPRESSION_WINDOW_S = 0.1
    FINAL_SCROLL_SYNC_CALLBACK_SUPPRESSION_EPSILON = 0.002
    RESIZE_NEGLIGIBLE_DELTA_PX = 6
    ROTATE_ANCHOR_DEBOUNCE_MS = 350

    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        self.view = PdfMergeView(master)
        self.model = MergeModel()
        self.preview_service = PreviewService(cache_size=120)

        self.preview_zoom = self.DEFAULT_ZOOM
        self._pending_resize_after: Optional[str] = None
        self._pending_zoom_after: Optional[str] = None
        self._pending_final_resize_settle_after: Optional[str] = None
        self._pending_final_scroll_render_after: Optional[str] = None
        self._pending_final_reconcile_after: Optional[str] = None
        self._last_preview_render_key: Optional[tuple[object, ...]] = None
        self._last_preview_canvas_size: tuple[int, int] = (0, 0)
        self._preview_image_refs: list[ImageTk.PhotoImage] = []
        self._final_preview_pages: list[FinalPreviewPage] = []
        self._final_preview_offsets: list[int] = [0]
        self._final_preview_total_height = 0
        self._final_preview_visible_indices: set[int] = set()
        self._last_final_render_signature: Optional[tuple[object, ...]] = None
        self._final_preview_anchor_fraction = 0.0
        self._final_preview_last_scroll_render_anchor = 0.0
        self._final_preview_render_window: Optional[FinalPreviewRenderWindow] = None
        self._final_preview_syncing_scrollbar = False
        self._final_preview_last_synced_fraction: Optional[float] = None
        self._final_preview_last_sync_ts: Optional[float] = None
        self._final_preview_rendering = False
        self._final_preview_dynamic_overscan_enabled = self._bool_from_env(
            self.FINAL_PREVIEW_DYNAMIC_OVERSCAN_ENV,
            default=False,
        )
        self._final_preview_dynamic_scroll_debounce_enabled = self._bool_from_env(
            self.FINAL_SCROLL_DYNAMIC_DEBOUNCE_ENV,
            default=True,
        )
        self._final_preview_scroll_velocity_px_s = 0.0
        self._final_preview_last_scroll_event_ts: Optional[float] = None
        self._final_preview_last_logical_top: Optional[float] = None
        self._final_preview_last_scroll_render_ts: Optional[float] = None
        self._final_preview_velocity_bucket = "slow"
        self._final_preview_overscan_telemetry: dict[str, int] = {"slow": 0, "medium": 0, "fast": 0}
        self._rotate_anchor_indices: tuple[int, ...] | None = None
        self._rotate_anchor_after: Optional[str] = None
        self._rotate_anchor_expires_at: float = 0.0
        self._final_preview_selection_lock = False
        self._last_preview_mode: Optional[str] = self.view.preview_mode.get()
        self._fit_zoom_page_dimensions_pt_cache: dict[tuple[str, int], tuple[float, float]] = {}
        self._fit_zoom_measured_pixel_cache: dict[tuple[str, int, int], tuple[int, int, float]] = {}

        self.render_worker = RenderWorker()
        self._render_request_sequence = 0
        self._active_render_generation = 0
        self._final_render_jobs: dict[int, dict[int, tuple[str, int, int]]] = {}
        self._final_render_results: dict[int, dict[int, ImageTk.PhotoImage]] = {}
        self._final_render_job_meta: dict[int, dict[str, object]] = {}
        self._pending_render_poll_after: Optional[str] = None

        self.final_preview_controller = FinalPreviewController(self)

        self.view.open_handler = self.on_open_pdfs
        self.view.move_up_handler = self.on_move_up
        self.view.move_down_handler = self.on_move_down
        self.view.remove_handler = self.on_remove_selected
        self.view.clear_handler = self.on_clear_all
        self.view.reverse_selected_handler = self.on_reverse_selected
        self.view.reverse_all_handler = self.on_reverse_all
        self.view.merge_handler = self.on_merge_export
        self.view.rotate_left_handler = self.on_rotate_left
        self.view.rotate_right_handler = self.on_rotate_right
        self.view.prev_handler = self.on_prev_preview
        self.view.next_handler = self.on_next_preview
        self.view.selection_handler = self.update_preview
        self.view.preview_mode_handler = self.update_preview
        self.view.zoom_in_handler = self.on_zoom_in
        self.view.zoom_out_handler = self.on_zoom_out
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
        self.view.page_list.bind("<ButtonRelease-1>", self.on_user_list_selection_change, add="+")
        self.view.page_list.bind("<KeyRelease>", self.on_user_list_selection_change, add="+")
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)
        self.view.preview_panel.bind("<Configure>", self.on_preview_panel_resize)
        self.view.preview_canvas.configure(yscrollcommand=self._on_preview_canvas_yscroll)

        self.refresh_list()

    @staticmethod
    def _bool_from_env(key: str, *, default: bool) -> bool:
        raw = os.environ.get(key)
        if raw is None:
            return default
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    def on_close(self) -> None:
        if self._pending_resize_after is not None:
            self.master.after_cancel(self._pending_resize_after)
            self._pending_resize_after = None
        if self._pending_zoom_after is not None:
            self.master.after_cancel(self._pending_zoom_after)
            self._pending_zoom_after = None
        if self._pending_final_resize_settle_after is not None:
            self.master.after_cancel(self._pending_final_resize_settle_after)
            self._pending_final_resize_settle_after = None
        if self._pending_final_scroll_render_after is not None:
            self.master.after_cancel(self._pending_final_scroll_render_after)
            self._pending_final_scroll_render_after = None
        if self._pending_final_reconcile_after is not None:
            self.master.after_cancel(self._pending_final_reconcile_after)
            self._pending_final_reconcile_after = None
        if self._rotate_anchor_after is not None:
            self.master.after_cancel(self._rotate_anchor_after)
            self._rotate_anchor_after = None
        if self._pending_render_poll_after is not None:
            self.master.after_cancel(self._pending_render_poll_after)
            self._pending_render_poll_after = None
        self.render_worker.close()
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
            except PdfSourceNotFoundError:
                messagebox.showerror(
                    "Could not open PDF",
                    f"File not found: {Path(filepath).name}",
                )
                continue
            except PdfLoadError as exc:
                messagebox.showerror("Could not open PDF", f"Failed to load {Path(filepath).name}:\n{exc}")
                continue
            except Exception as exc:
                messagebox.showerror(
                    "Could not open PDF",
                    f"Unexpected error loading {Path(filepath).name}:\n{exc}",
                )
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
        self._clear_rotate_anchor()
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
        self._clear_rotate_anchor()
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
        if self._pending_render_poll_after is not None:
            self.master.after_cancel(self._pending_render_poll_after)
            self._pending_render_poll_after = None
        self.render_worker.close()
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


    def on_rotate_left(self) -> None:
        indices = self._rotation_target_indices()
        if not indices:
            return
        self.refresh_list(select_indices=self.model.rotate_counterclockwise(indices))

    def on_rotate_right(self) -> None:
        indices = self._rotation_target_indices()
        if not indices:
            return
        self.refresh_list(select_indices=self.model.rotate_clockwise(indices))

    def on_user_list_selection_change(self, _event: tk.Event) -> None:
        self._clear_rotate_anchor()

    def is_final_preview_selection_locked(self) -> bool:
        return self._final_preview_selection_lock

    def _rotation_target_indices(self) -> list[int]:
        indices = self.selected_indices()
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL:
            self._clear_rotate_anchor()
            return indices
        if not indices and self._rotate_anchor_indices is None:
            return []

        now = time.monotonic()
        if self._rotate_anchor_indices is not None and now < self._rotate_anchor_expires_at:
            target = list(self._rotate_anchor_indices)
        else:
            if not indices:
                return []
            target = sorted(set(indices))
            self._rotate_anchor_indices = tuple(target)

        self._final_preview_selection_lock = True
        self._rotate_anchor_expires_at = now + (self.ROTATE_ANCHOR_DEBOUNCE_MS / 1000)
        self._schedule_rotate_anchor_reset()
        return target

    def _schedule_rotate_anchor_reset(self) -> None:
        if self._rotate_anchor_after is not None:
            self.master.after_cancel(self._rotate_anchor_after)
        self._rotate_anchor_after = self.master.after(self.ROTATE_ANCHOR_DEBOUNCE_MS, self._clear_rotate_anchor)

    def _clear_rotate_anchor(self) -> None:
        self._rotate_anchor_indices = None
        self._rotate_anchor_expires_at = 0.0
        self._final_preview_selection_lock = False
        if self._rotate_anchor_after is not None:
            self.master.after_cancel(self._rotate_anchor_after)
            self._rotate_anchor_after = None

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
        except PdfMergeWriteError as exc:
            messagebox.showerror("Merge failed", f"Could not write merged PDF:\n{exc}")
            return
        except Exception as exc:
            messagebox.showerror("Merge failed", f"Unexpected merge failure:\n{exc}")
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
        widget_builder: Callable[[], list[PreviewWidgetSpec]],
        reset_scroll: bool = True,
        preserve_scroll: bool = False,
    ) -> None:
        telemetry = get_telemetry()
        telemetry.increment("preview_show_widgets_calls")
        scroll_x = 0.0
        scroll_y = 0.0
        if preserve_scroll:
            scroll_x = self.view.preview_canvas.xview()[0]
            scroll_y = self.view.preview_canvas.yview()[0]

        with telemetry.time_block("preview_show_widgets"):
            widget_specs = widget_builder()
            self.view.show_preview_widget_specs(widget_specs)
            self.view.refresh_preview_layout()

        if preserve_scroll:
            self.view.preview_canvas.xview_moveto(scroll_x)
            self.view.preview_canvas.yview_moveto(scroll_y)
        elif reset_scroll:
            self.view.reset_preview_scroll()

    def show_preview_text(self, text: str) -> None:
        self._preview_image_refs = []
        def build() -> list[PreviewWidgetSpec]:
            return [{"kind": "label", "text": text, "padding": 24}]

        self._show_preview_widgets(build)

    def show_preview_image(self, image: ImageTk.PhotoImage, reset_scroll: bool = True) -> None:
        self._preview_image_refs = [image]
        self._final_preview_visible_indices = set()
        self._last_final_render_signature = None
        def build() -> list[PreviewWidgetSpec]:
            return [{"kind": "image", "image": image}]

        self._show_preview_widgets(build, reset_scroll=reset_scroll)

    def show_preview_images(self, images: list[ImageTk.PhotoImage], preserve_scroll: bool = False) -> None:
        self._preview_image_refs = list(images)
        self._final_preview_visible_indices = set()
        self._last_final_render_signature = None
        def build() -> list[PreviewWidgetSpec]:
            return [{"kind": "image", "image": image} for image in images]

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

    def _resolve_zoom(self, source_path: str, page_index: int, rotation_degrees: int = 0) -> tuple[float, ImageTk.PhotoImage]:
        base_zoom = self.preview_zoom
        telemetry = get_telemetry()
        normalized_rotation = rotation_degrees % 360
        if not self.view.fit_preview.get():
            rendered = self.preview_service.render(source_path, page_index, base_zoom, rotation_degrees=rotation_degrees)
            self._remember_measured_fit_size(source_path, page_index, normalized_rotation, rendered, base_zoom)
            return base_zoom, rendered

        panel_width, panel_height = self._panel_size()
        estimated_zoom = self._estimate_fit_zoom(source_path, page_index, normalized_rotation, panel_width, panel_height)
        if estimated_zoom is not None:
            fit_zoom = self._clamp_zoom(estimated_zoom)
            rendered = self.preview_service.render(source_path, page_index, fit_zoom, rotation_degrees=rotation_degrees)
            self._remember_measured_fit_size(source_path, page_index, normalized_rotation, rendered, fit_zoom)
            telemetry.increment("fit_mode_single_render")
            return fit_zoom, rendered

        telemetry.increment("fit_mode_double_render_attempts")
        rendered = self.preview_service.render(source_path, page_index, base_zoom, rotation_degrees=rotation_degrees)
        self._remember_measured_fit_size(source_path, page_index, normalized_rotation, rendered, base_zoom)
        width_ratio = panel_width / max(rendered.width(), 1)
        height_ratio = panel_height / max(rendered.height(), 1)
        fit_ratio = min(width_ratio, height_ratio)
        fit_zoom = self._clamp_zoom(base_zoom * fit_ratio)
        if abs(fit_zoom - base_zoom) < 0.01:
            return base_zoom, rendered
        fit_rendered = self.preview_service.render(source_path, page_index, fit_zoom, rotation_degrees=rotation_degrees)
        self._remember_measured_fit_size(source_path, page_index, normalized_rotation, fit_rendered, fit_zoom)
        return fit_zoom, fit_rendered

    def _remember_measured_fit_size(
        self,
        source_path: str,
        page_index: int,
        rotation_degrees: int,
        rendered: ImageTk.PhotoImage,
        zoom: float,
    ) -> None:
        if zoom <= 0:
            return
        self._fit_zoom_measured_pixel_cache[(source_path, page_index, rotation_degrees)] = (
            rendered.width(),
            rendered.height(),
            zoom,
        )

    def _estimate_fit_zoom(
        self,
        source_path: str,
        page_index: int,
        rotation_degrees: int,
        panel_width: int,
        panel_height: int,
    ) -> float | None:
        exact_dimensions = self._get_cached_page_dimensions_points(source_path, page_index, rotation_degrees)
        if exact_dimensions is not None:
            page_width, page_height = exact_dimensions
            return min(panel_width / max(page_width, 1.0), panel_height / max(page_height, 1.0))

        measured = self._fit_zoom_measured_pixel_cache.get((source_path, page_index, rotation_degrees))
        if measured is None:
            return None

        measured_width_px, measured_height_px, measured_zoom = measured
        if measured_zoom <= 0:
            return None

        unscaled_width = measured_width_px / measured_zoom
        unscaled_height = measured_height_px / measured_zoom
        return min(panel_width / max(unscaled_width, 1.0), panel_height / max(unscaled_height, 1.0))

    def _get_cached_page_dimensions_points(
        self,
        source_path: str,
        page_index: int,
        rotation_degrees: int,
    ) -> tuple[float, float] | None:
        key = (source_path, page_index)
        cached = self._fit_zoom_page_dimensions_pt_cache.get(key)
        if cached is None:
            try:
                from pypdf import PdfReader

                reader = PdfReader(source_path)
                page = reader.pages[page_index]
                media_box = page.mediabox
                page_width = float(media_box.width)
                page_height = float(media_box.height)
                cached = (page_width, page_height)
                self._fit_zoom_page_dimensions_pt_cache[key] = cached
            except Exception:
                return None

        if rotation_degrees % 180 == 90:
            return (cached[1], cached[0])
        return cached

    def on_zoom_in(self) -> None:
        self.preview_zoom = self._zoom_step_from_fit(direction=1)
        self._deactivate_fit_preview()
        self._update_zoom_label()
        self._schedule_zoom_render()

    def on_zoom_out(self) -> None:
        self.preview_zoom = self._zoom_step_from_fit(direction=-1)
        self._deactivate_fit_preview()
        self._update_zoom_label()
        self._schedule_zoom_render()

    def _zoom_step_from_fit(self, direction: int) -> float:
        target_zoom = self.preview_zoom
        if self.view.fit_preview.get() and self.model.sequence:
            idx = self.selected_index()
            if idx is None:
                idx = 0
            page = self.model.sequence[idx]
            try:
                effective_zoom, _ = self._resolve_zoom(page.source_path, page.page_index, page.rotation_degrees)
                target_zoom = effective_zoom
            except Exception:
                target_zoom = self.preview_zoom
        return self._clamp_zoom(target_zoom + (direction * self.ZOOM_STEP))

    def on_ctrl_wheel_zoom(self, wheel_units: int) -> None:
        next_zoom = self._clamp_zoom(self.preview_zoom + (-wheel_units * self.ZOOM_STEP))
        if abs(next_zoom - self.preview_zoom) < 0.001:
            return
        self.preview_zoom = next_zoom
        self._deactivate_fit_preview()
        self._update_zoom_label()
        self._schedule_zoom_render()

    def _schedule_zoom_render(self) -> None:
        if self._pending_zoom_after is not None:
            self.master.after_cancel(self._pending_zoom_after)
        self._pending_zoom_after = self.master.after(
            self.ZOOM_RENDER_DEBOUNCE_MS,
            self._on_zoom_render_debounced,
        )

    def _on_zoom_render_debounced(self) -> None:
        self._pending_zoom_after = None
        self.update_preview()

    def _deactivate_fit_preview(self) -> None:
        if self.view.fit_preview.get():
            self.view.fit_preview.set(False)

    def on_toggle_fit_preview(self) -> None:
        self.update_preview()

    def _log_preview_debug(self, message: str) -> None:
        return

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

    def _velocity_bucket_for_scroll_velocity(self, scroll_velocity_px_s: float) -> str:
        velocity = max(float(scroll_velocity_px_s), 0.0)
        if velocity < self.FINAL_PREVIEW_OVERSCAN_SLOW_MAX_VELOCITY_PX_S:
            return "slow"
        if velocity <= self.FINAL_PREVIEW_OVERSCAN_MEDIUM_MAX_VELOCITY_PX_S:
            return "medium"
        return "fast"

    def compute_overscan_pages(self, scroll_velocity_px_s: float) -> int:
        bucket = self._velocity_bucket_for_scroll_velocity(scroll_velocity_px_s)
        if bucket == "slow":
            requested = self.FINAL_PREVIEW_OVERSCAN_PAGES
        elif bucket == "medium":
            requested = self.FINAL_PREVIEW_OVERSCAN_MEDIUM_PAGES
        else:
            requested = self.FINAL_PREVIEW_OVERSCAN_FAST_PAGES

        overscan = max(self.FINAL_PREVIEW_MIN_OVERSCAN_PAGES, min(requested, self.FINAL_PREVIEW_MAX_OVERSCAN_PAGES))
        self._final_preview_velocity_bucket = bucket
        self._final_preview_overscan_telemetry[bucket] = self._final_preview_overscan_telemetry.get(bucket, 0) + 1
        return overscan

    def compute_debounce_ms(self, scroll_velocity_px_s: float) -> int:
        if not self._final_preview_dynamic_scroll_debounce_enabled:
            return self.FINAL_SCROLL_RENDER_DEBOUNCE_MS

        bucket = self._velocity_bucket_for_scroll_velocity(scroll_velocity_px_s)
        if bucket == "slow":
            return self.FINAL_SCROLL_RENDER_DEBOUNCE_SLOW_MS
        if bucket == "medium":
            return self.FINAL_SCROLL_RENDER_DEBOUNCE_MEDIUM_MS
        return self.FINAL_SCROLL_RENDER_DEBOUNCE_FAST_MS

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

    def _build_spacer_widgets(self, total_height: int) -> list[PreviewWidgetSpec]:
        if total_height <= 0:
            return []

        chunk_limit = self._spacer_chunk_limit()
        remaining = total_height
        widgets: list[PreviewWidgetSpec] = []
        while remaining > 0:
            chunk_height = min(remaining, chunk_limit)
            widgets.append({"kind": "spacer", "height": chunk_height, "pady": 0})
            remaining -= chunk_height
        return widgets

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
        return self.final_preview_controller._rendered_scroll_fraction_for_anchor()

    def _on_preview_canvas_yscroll(self, first: str, last: str) -> None:
        self.final_preview_controller.on_preview_canvas_yscroll(first, last)

    def _render_final_preview_from_scroll(self) -> None:
        self.final_preview_controller.render_final_preview_from_scroll()

    def _sync_canvas_scroll_to_fraction(self, fraction: float) -> bool:
        return self.final_preview_controller.sync_canvas_scroll_to_fraction(fraction)

    def render_preview_image(self, source_path: str, page_index: int, rotation_degrees: int = 0) -> Optional[ImageTk.PhotoImage]:
        try:
            effective_zoom, rendered = self._resolve_zoom(source_path, page_index, rotation_degrees)
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


    def _resolve_render_zoom_for_request(self, source_path: str, page_index: int, rotation_degrees: int = 0) -> float:
        if not self.view.fit_preview.get():
            return self.preview_zoom
        panel_width, panel_height = self._panel_size()
        estimated_zoom = self._estimate_fit_zoom(source_path, page_index, rotation_degrees % 360, panel_width, panel_height)
        if estimated_zoom is None:
            return self.preview_zoom
        return self._clamp_zoom(estimated_zoom)

    def queue_final_preview_render(
        self,
        *,
        requests: dict[int, tuple[str, int, int]],
        meta: dict[str, object],
    ) -> int:
        self._active_render_generation += 1
        generation_id = self._active_render_generation
        self._render_request_sequence += 1
        self._final_render_jobs[generation_id] = dict(requests)
        self._final_render_results[generation_id] = {}
        self._final_render_job_meta[generation_id] = dict(meta)
        for list_idx, (source_path, page_index, rotation_degrees) in requests.items():
            zoom = self._resolve_render_zoom_for_request(source_path, page_index, rotation_degrees)
            self.render_worker.submit(
                RenderRequest(
                    generation_id=generation_id,
                    request_id=list_idx,
                    source_path=source_path,
                    page_index=page_index,
                    rotation_degrees=rotation_degrees,
                    zoom=zoom,
                )
            )
        self._schedule_render_result_poll()
        return generation_id

    def _schedule_render_result_poll(self) -> None:
        if self._pending_render_poll_after is not None:
            return
        self._pending_render_poll_after = self.master.after(10, self._poll_render_results)

    def _poll_render_results(self) -> None:
        self._pending_render_poll_after = None
        had_results = False
        for result in self.render_worker.poll_results():
            had_results = True
            if result.generation_id != self._active_render_generation:
                continue
            expected = self._final_render_jobs.get(result.generation_id)
            if expected is None or result.request_id not in expected:
                continue
            if result.error is not None:
                continue
            photo = build_photo_image(result, ImageTk)
            self._final_render_results[result.generation_id][result.request_id] = photo
            source_path, page_index, rotation_degrees = expected[result.request_id]
            self._remember_measured_fit_size(source_path, page_index, rotation_degrees % 360, photo, self._resolve_render_zoom_for_request(source_path, page_index, rotation_degrees))

        active = self._active_render_generation
        expected = self._final_render_jobs.get(active)
        completed = self._final_render_results.get(active)
        if expected is not None and completed is not None and len(completed) == len(expected):
            self.final_preview_controller.apply_completed_final_render_generation(active)
            self._final_render_jobs.pop(active, None)
            self._final_render_results.pop(active, None)
            self._final_render_job_meta.pop(active, None)
            return

        if had_results or self._final_render_jobs.get(self._active_render_generation):
            self._schedule_render_result_poll()

    def get_completed_final_render_images(self, generation_id: int) -> dict[int, ImageTk.PhotoImage]:
        return dict(self._final_render_results.get(generation_id, {}))

    def get_final_render_meta(self, generation_id: int) -> dict[str, object]:
        return dict(self._final_render_job_meta.get(generation_id, {}))


    def _sequence_signature(self) -> tuple[tuple[str, int, int], ...]:
        return tuple((page.source_path, page.page_index, page.rotation_degrees) for page in self.model.sequence)

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
        self.final_preview_controller.build_final_preview_model()

    def _recompute_final_preview_offsets(self) -> None:
        self.final_preview_controller.recompute_final_preview_offsets()

    def _visible_virtual_window(self) -> tuple[int, int]:
        return self.final_preview_controller.visible_virtual_window()

    def _visible_page_range(self, top: int, bottom: int) -> tuple[int, int]:
        return self.final_preview_controller.visible_page_range(top, bottom)

    def _render_virtual_final_preview(self, preserve_anchor: bool) -> None:
        self.final_preview_controller.render_virtual_final_preview(preserve_anchor)

    def update_preview(self) -> None:
        current_mode = self.view.preview_mode.get()
        if self._last_preview_mode != current_mode:
            self._clear_rotate_anchor()
            self._last_preview_mode = current_mode

        if not self.model.sequence:
            self._last_preview_render_key = None
            self._last_final_render_signature = None
            self.view.preview_caption.configure(text="No pages loaded")
            self._update_zoom_label()
            self.show_preview_text("Open one or more PDFs to begin.")
            return

        if current_mode == self.view.PREVIEW_SINGLE:
            self._final_preview_pages = []
            self._final_preview_visible_indices = set()
            self._last_final_render_signature = None
            idx = self.selected_index()
            if idx is None:
                idx = 0
                self.set_selected_indices([idx])
            page = self.model.sequence[idx]
            self.view.preview_caption.configure(text=f"Single Page ({idx + 1}/{len(self.model.sequence)})")
            preview_key = self._current_preview_key(self.view.PREVIEW_SINGLE, selected_index=idx)
            if preview_key == self._last_preview_render_key:
                return

            rendered = self.render_preview_image(page.source_path, page.page_index, page.rotation_degrees)
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
