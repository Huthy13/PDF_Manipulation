from __future__ import annotations

import tkinter as tk
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional, Sequence

from PIL import Image, ImageTk

from ..model import MergeModel
from ..preview import PreviewDependencyUnavailable, PreviewRenderError
from ..services.preview_pipeline import PreviewRenderPipeline, RenderRequest, RenderResult
from ..services.preview_service import PreviewService, PrimaryCacheKey, UiCacheKey
from .view import PdfMergeView


@dataclass
class FinalPreviewPage:
    source_path: str
    page_index: int
    intrinsic_width: float
    intrinsic_height: float
    estimated_height: int
    logical_height: int = 1


class PdfMergeController:
    MIN_ZOOM = 0.4
    MAX_ZOOM = 4.0
    ZOOM_STEP = 0.2
    DEFAULT_ZOOM = 1.5
    FINAL_PREVIEW_PAGE_GAP = 12
    FINAL_PREVIEW_OVERSCAN_PAGES = 2
    FINAL_PREVIEW_ESTIMATED_PAGE_HEIGHT = 1300
    RESIZE_DEBOUNCE_MS = 120
    FINAL_RESIZE_DEBOUNCE_MS = 180
    FINAL_RESIZE_SETTLE_MS = 240
    FINAL_SCROLL_RENDER_DEBOUNCE_MS = 24
    RESIZE_NEGLIGIBLE_DELTA_PX = 6

    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        self.view = PdfMergeView(master)
        self.model = MergeModel()
        self.preview_service = PreviewService(
            cache_size=120,
            max_cache_bytes=300 * 1024 * 1024,
            offscreen_cache_size=36,
            offscreen_cache_bytes=90 * 1024 * 1024,
            ui_cache_size=72,
            ui_cache_bytes=220 * 1024 * 1024,
            ui_offscreen_cache_size=20,
            ui_offscreen_cache_bytes=64 * 1024 * 1024,
        )

        self.preview_zoom = self.DEFAULT_ZOOM
        self._pending_resize_after: Optional[str] = None
        self._pending_final_resize_settle_after: Optional[str] = None
        self._pending_final_scroll_render_after: Optional[str] = None
        self._last_preview_render_key: Optional[tuple[object, ...]] = None
        self._last_preview_canvas_size: tuple[int, int] = (0, 0)
        self._last_fit_panel_size: Optional[tuple[int, int]] = None
        self._final_preview_zoom: float = self.preview_zoom
        self._preview_image_refs: list[ImageTk.PhotoImage] = []
        self._final_preview_pages: list[FinalPreviewPage] = []
        self._final_preview_offsets: list[int] = [0]
        self._final_preview_total_height = 0
        self._final_preview_visible_indices: set[int] = set()
        self._final_preview_anchor_page_index = 0
        self._final_preview_anchor_offset_px_within_page = 0
        self._final_preview_syncing_scrollbar = False
        self._final_preview_rendering = False
        self._final_preview_generation = 0
        self._final_preview_active_range: tuple[int, int] = (0, -1)
        self._final_preview_images_by_index: dict[int, ImageTk.PhotoImage] = {}
        self._final_preview_decoded_by_index: dict[int, tuple[PrimaryCacheKey, Image.Image]] = {}
        self._final_preview_render_errors: set[int] = set()
        self._final_preview_pending_indices: set[int] = set()
        self._final_preview_pool_top_spacer: Optional[ttk.Frame] = None
        self._final_preview_pool_bottom_spacer: Optional[ttk.Frame] = None
        self._final_preview_row_pool: list[ttk.Frame] = []
        self._final_preview_row_labels: list[tk.Label] = []
        self._final_preview_row_bound_indices: list[Optional[int]] = []
        self._final_preview_pipeline = PreviewRenderPipeline(on_result=self._on_final_preview_result)
        self._last_preview_mode = self.view.preview_mode.get()

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
        self._advance_final_preview_generation()
        self._final_preview_pipeline.stop()
        self._preview_image_refs = []
        self._final_preview_pages = []
        self._final_preview_images_by_index = {}
        self._final_preview_decoded_by_index = {}
        self._clear_final_preview_row_pool()
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
        self._advance_final_preview_generation()
        self._final_preview_pipeline.stop()
        self._preview_image_refs = []
        self._final_preview_pages = []
        self._final_preview_images_by_index = {}
        self._final_preview_decoded_by_index = {}
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
        self._clear_final_preview_row_pool()
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
        self._clear_final_preview_row_pool()
        def build() -> list[tk.Widget]:
            preview = tk.Label(self.view.preview_content, image=image, bd=0, highlightthickness=0)
            preview.image = image
            return [preview]

        self._show_preview_widgets(build, reset_scroll=reset_scroll)

    def show_preview_images(self, images: list[ImageTk.PhotoImage], preserve_scroll: bool = False) -> None:
        self._preview_image_refs = list(images)
        self._final_preview_visible_indices = set()
        self._clear_final_preview_row_pool()
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

    def _widget_scale_context(self) -> tuple[float]:
        scaling = float(self.master.tk.call("tk", "scaling"))
        return (round(scaling, 3),)

    def _visible_decoded_keys(self) -> set[PrimaryCacheKey]:
        keys: set[PrimaryCacheKey] = set()
        if self.view.preview_mode.get() == self.view.PREVIEW_FINAL:
            for idx in self._final_preview_visible_indices:
                descriptor = self._final_preview_decoded_by_index.get(idx)
                if descriptor is not None:
                    keys.add(descriptor[0])
        return keys

    def _visible_ui_keys(self) -> set[UiCacheKey]:
        context = self._widget_scale_context()
        keys: set[UiCacheKey] = set()
        if self.view.preview_mode.get() == self.view.PREVIEW_FINAL:
            for idx in self._final_preview_visible_indices:
                descriptor = self._final_preview_decoded_by_index.get(idx)
                if descriptor is not None:
                    keys.add((id(descriptor[1]), context))
        return keys

    def _trim_preview_caches(self) -> None:
        self.preview_service.trim_to_budget(
            visible_decoded_keys=self._visible_decoded_keys(),
            visible_ui_keys=self._visible_ui_keys(),
        )

    def _resolve_zoom(self, source_path: str, page_index: int) -> float:
        base_zoom = self.preview_zoom
        if not self.view.fit_preview.get():
            self._last_fit_panel_size = None
            return base_zoom

        panel_width, panel_height = self._panel_size()
        intrinsic_width, intrinsic_height = self.preview_service.get_page_dimensions(source_path, page_index)
        width_ratio = panel_width / max(intrinsic_width, 1.0)
        height_ratio = panel_height / max(intrinsic_height, 1.0)
        fit_ratio = min(width_ratio, height_ratio)
        self._last_fit_panel_size = (panel_width, panel_height)
        return self._clamp_zoom(base_zoom * fit_ratio)

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
        self._last_fit_panel_size = None
        self.update_preview()

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
        self._request_final_preview_render(preserve_anchor=True, refresh_generation=True)

    def _on_resize_debounced(self) -> None:
        self._pending_resize_after = None
        if self.view.preview_mode.get() == self.view.PREVIEW_FINAL:
            current_size = (self.view.preview_canvas.winfo_width(), self.view.preview_canvas.winfo_height())
            previous_size = self._last_preview_canvas_size
            self._last_preview_canvas_size = current_size

            if self._is_negligible_resize(previous_size, current_size):
                return
            self._update_final_preview_window_state()
            self._recompute_final_preview_offsets()
            self._schedule_final_resize_settled_render()
        elif self.view.fit_preview.get():
            self.update_preview()

    def _on_preview_canvas_yscroll(self, first: str, last: str) -> None:
        self.view.preview_vscroll.set(first, last)
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL:
            return
        if self._final_preview_syncing_scrollbar:
            return
        try:
            first_fraction = float(first)
        except ValueError:
            return
        self._set_virtual_anchor_from_fraction(max(0.0, min(1.0, first_fraction)))
        if self._pending_final_scroll_render_after is not None:
            self.master.after_cancel(self._pending_final_scroll_render_after)
        self._pending_final_scroll_render_after = self.master.after(
            self.FINAL_SCROLL_RENDER_DEBOUNCE_MS,
            self._render_final_preview_from_scroll,
        )

    def _render_final_preview_from_scroll(self) -> None:
        self._pending_final_scroll_render_after = None
        if self.view.preview_mode.get() != self.view.PREVIEW_FINAL:
            return
        self._request_final_preview_render(preserve_anchor=True, refresh_generation=True)

    def render_preview_image(self, source_path: str, page_index: int) -> Optional[ImageTk.PhotoImage]:
        try:
            effective_zoom = self._resolve_zoom(source_path, page_index)
            self._update_zoom_label(effective_zoom=effective_zoom)
            _decoded_key, rendered = self.preview_service.get_decoded_image(source_path, page_index, effective_zoom)
            image = self.preview_service.get_ui_image(rendered, self._widget_scale_context())
            self._trim_preview_caches()
            return image
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

        self._final_preview_images_by_index = {}
        self._final_preview_decoded_by_index = {}
        self._trim_preview_caches()

        previous_heights = {
            (page.source_path, page.page_index): page.estimated_height
            for page in self._final_preview_pages
        }
        previous_logical_heights = {
            (page.source_path, page.page_index): page.logical_height
            for page in self._final_preview_pages
        }
        pages: list[FinalPreviewPage] = []
        for source_path, page_index in sequence:
            intrinsic_width, intrinsic_height = self.preview_service.get_page_dimensions(source_path, page_index)
            estimated_height = previous_heights.get(
                (source_path, page_index),
                self.FINAL_PREVIEW_ESTIMATED_PAGE_HEIGHT,
            )
            pages.append(
                FinalPreviewPage(
                    source_path=source_path,
                    page_index=page_index,
                    intrinsic_width=intrinsic_width,
                    intrinsic_height=intrinsic_height,
                    estimated_height=estimated_height,
                    logical_height=previous_logical_heights.get((source_path, page_index), estimated_height),
                )
            )
        self._final_preview_pages = pages
        self._recompute_final_preview_offsets()

    def _recompute_final_preview_offsets(self) -> None:
        if not self._final_preview_pages:
            self._final_preview_offsets = [0]
            self._final_preview_total_height = 0
            return

        offsets = [0]
        running = 0
        panel_width, _panel_height = self._panel_size()
        target_zoom = self._final_preview_zoom
        if self.view.fit_preview.get():
            zoom_candidates = [
                panel_width / max(page.intrinsic_width, 1.0)
                for page in self._final_preview_pages
            ]
            target_zoom = self._clamp_zoom(min(zoom_candidates) if zoom_candidates else self.preview_zoom)
        self._final_preview_zoom = target_zoom

        for page in self._final_preview_pages:
            projected_height = int(round(page.intrinsic_height * target_zoom))
            page.logical_height = max(projected_height, page.estimated_height, 1)
            running += page.logical_height + self.FINAL_PREVIEW_PAGE_GAP
            offsets.append(running)
        self._final_preview_offsets = offsets
        self._final_preview_total_height = running
        self._clamp_virtual_anchor_to_bounds()

    def _visible_virtual_window(self) -> tuple[int, int]:
        viewport_height = max(self.view.preview_canvas.winfo_height(), 1)
        virtual_top = self._virtual_top_from_anchor()
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

    def _core_visible_page_range(self, top: int, bottom: int) -> tuple[int, int]:
        if not self._final_preview_pages:
            return 0, -1
        start = max(bisect_right(self._final_preview_offsets, top) - 1, 0)
        end = min(bisect_right(self._final_preview_offsets, bottom) - 1, len(self._final_preview_pages) - 1)
        return start, end

    def _max_viewport_start(self) -> int:
        viewport_height = max(self.view.preview_canvas.winfo_height(), 1)
        return max(self._final_preview_total_height - viewport_height, 0)

    def _fraction_for_virtual_top(self, virtual_top: int) -> float:
        max_start = self._max_viewport_start()
        if max_start <= 0:
            return 0.0
        return max(0.0, min(1.0, virtual_top / max_start))

    def _virtual_top_from_anchor(self) -> int:
        if not self._final_preview_pages:
            return 0
        page_index = max(0, min(self._final_preview_anchor_page_index, len(self._final_preview_pages) - 1))
        page_top = self._final_preview_offsets[page_index]
        page_height = max(self._final_preview_pages[page_index].logical_height, 1)
        offset = max(0, min(self._final_preview_anchor_offset_px_within_page, page_height - 1))
        return max(0, min(page_top + offset, self._max_viewport_start()))

    def _set_virtual_anchor(self, virtual_top: int) -> None:
        if not self._final_preview_pages:
            self._final_preview_anchor_page_index = 0
            self._final_preview_anchor_offset_px_within_page = 0
            return
        clamped_top = max(0, min(virtual_top, self._max_viewport_start()))
        anchor_index = max(0, min(bisect_right(self._final_preview_offsets, clamped_top) - 1, len(self._final_preview_pages) - 1))
        page_top = self._final_preview_offsets[anchor_index]
        page_height = max(self._final_preview_pages[anchor_index].logical_height, 1)
        self._final_preview_anchor_page_index = anchor_index
        self._final_preview_anchor_offset_px_within_page = max(0, min(clamped_top - page_top, page_height - 1))

    def _set_virtual_anchor_from_fraction(self, fraction: float) -> None:
        self._set_virtual_anchor(int(round(max(0.0, min(1.0, fraction)) * self._max_viewport_start())))

    def _clamp_virtual_anchor_to_bounds(self) -> None:
        self._set_virtual_anchor(self._virtual_top_from_anchor())

    def _advance_final_preview_generation(self) -> int:
        self._final_preview_generation += 1
        self._final_preview_pipeline.set_active_generation(self._final_preview_generation)
        self._final_preview_pending_indices = set()
        self._final_preview_rendering = False
        return self._final_preview_generation

    def _request_final_preview_render(self, preserve_anchor: bool, refresh_generation: bool) -> None:
        if not self._final_preview_pages:
            self.show_preview_text("Open one or more PDFs to begin.")
            return

        if not preserve_anchor:
            self._set_virtual_anchor(0)

        if refresh_generation:
            generation = self._advance_final_preview_generation()
        else:
            generation = self._final_preview_generation

        top, bottom = self._visible_virtual_window()
        visible_start, visible_end = self._core_visible_page_range(top, bottom)
        if visible_end < visible_start:
            return

        start_idx, end_idx = self._visible_page_range(top, bottom)
        self._final_preview_active_range = (start_idx, end_idx)
        self._final_preview_visible_indices = set(range(start_idx, end_idx + 1))

        self._final_preview_syncing_scrollbar = True
        try:
            self.view.preview_canvas.yview_moveto(self._fraction_for_virtual_top(top))
        finally:
            self._final_preview_syncing_scrollbar = False

        request_indices = [
            idx for idx in range(start_idx, end_idx + 1) if idx not in self._final_preview_decoded_by_index
        ]
        if not request_indices:
            self._compose_final_preview_widgets(allow_fast_path=True)
            self._trim_preview_caches()
            return

        self._final_preview_pending_indices = set(request_indices)
        self._final_preview_rendering = True

        for idx in request_indices:
            descriptor = self._final_preview_pages[idx]
            priority = 0 if visible_start <= idx <= visible_end else 1
            self._final_preview_pipeline.submit(
                RenderRequest(
                    source_path=descriptor.source_path,
                    page_index=descriptor.page_index,
                    zoom=self._final_preview_zoom,
                    generation_id=generation,
                ),
                priority=priority,
            )

        self._compose_final_preview_widgets(allow_fast_path=False)

    def _clear_final_preview_row_pool(self) -> None:
        self._final_preview_pool_top_spacer = None
        self._final_preview_pool_bottom_spacer = None
        self._final_preview_row_pool = []
        self._final_preview_row_labels = []
        self._final_preview_row_bound_indices = []

    def _ensure_final_preview_row_pool(self, visible_count: int) -> None:
        if self._final_preview_pool_top_spacer is None or not self._final_preview_pool_top_spacer.winfo_exists():
            self._final_preview_pool_top_spacer = ttk.Frame(self.view.preview_content, height=0)
            self._final_preview_pool_top_spacer.grid_propagate(False)
        if self._final_preview_pool_bottom_spacer is None or not self._final_preview_pool_bottom_spacer.winfo_exists():
            self._final_preview_pool_bottom_spacer = ttk.Frame(self.view.preview_content, height=0)
            self._final_preview_pool_bottom_spacer.grid_propagate(False)

        while len(self._final_preview_row_pool) < visible_count:
            row_frame = ttk.Frame(self.view.preview_content, height=1)
            row_frame.grid_propagate(False)
            row_label = tk.Label(row_frame, bd=0, highlightthickness=0)
            row_label.pack(fill="both", expand=True)
            self._final_preview_row_pool.append(row_frame)
            self._final_preview_row_labels.append(row_label)
            self._final_preview_row_bound_indices.append(None)

        while len(self._final_preview_row_pool) > visible_count:
            row_frame = self._final_preview_row_pool.pop()
            self._final_preview_row_labels.pop()
            self._final_preview_row_bound_indices.pop()
            row_frame.destroy()

    def _bind_final_preview_row(self, pool_slot: int, page_index: int, images_by_index: dict[int, ImageTk.PhotoImage]) -> None:
        row_frame = self._final_preview_row_pool[pool_slot]
        row_label = self._final_preview_row_labels[pool_slot]
        image = images_by_index.get(page_index)
        if image is None:
            row_label.configure(image="", text="")
            row_label.image = None
            row_frame.configure(height=max(self._final_preview_pages[page_index].logical_height, 1))
        else:
            row_label.configure(image=image, text="")
            row_label.image = image
            row_frame.configure(height=max(image.height(), 1))
        self._final_preview_row_bound_indices[pool_slot] = page_index

    def _compose_final_preview_widgets(self, allow_fast_path: bool) -> None:
        start_idx, end_idx = self._final_preview_active_range
        if end_idx < start_idx:
            return

        top_spacer = self._final_preview_offsets[start_idx]
        bottom_spacer = max(self._final_preview_offsets[-1] - self._final_preview_offsets[end_idx + 1], 0)
        widget_context = self._widget_scale_context()

        images_by_index: dict[int, ImageTk.PhotoImage] = {}
        for idx in range(start_idx, end_idx + 1):
            descriptor = self._final_preview_decoded_by_index.get(idx)
            if descriptor is None:
                continue
            _, decoded = descriptor
            images_by_index[idx] = self.preview_service.get_ui_image(decoded, widget_context)

        self._final_preview_images_by_index = images_by_index

        visible_count = end_idx - start_idx + 1
        previous_bound = list(self._final_preview_row_bound_indices)
        self._ensure_final_preview_row_pool(visible_count)

        if self._final_preview_pool_top_spacer is not None:
            self._final_preview_pool_top_spacer.configure(height=top_spacer)
        if self._final_preview_pool_bottom_spacer is not None:
            self._final_preview_pool_bottom_spacer.configure(height=bottom_spacer)

        if allow_fast_path and previous_bound:
            prev_start = previous_bound[0]
            prev_end = previous_bound[-1]
            if prev_start is not None and prev_end is not None and len(previous_bound) == visible_count:
                offset = start_idx - prev_start
                if prev_end == end_idx - offset and 0 < abs(offset) < visible_count:
                    if offset > 0:
                        self._final_preview_row_pool = self._final_preview_row_pool[offset:] + self._final_preview_row_pool[:offset]
                        self._final_preview_row_labels = self._final_preview_row_labels[offset:] + self._final_preview_row_labels[:offset]
                        self._final_preview_row_bound_indices = (
                            self._final_preview_row_bound_indices[offset:] + self._final_preview_row_bound_indices[:offset]
                        )
                        for slot in range(visible_count - offset, visible_count):
                            self._bind_final_preview_row(slot, start_idx + slot, images_by_index)
                    else:
                        shift = abs(offset)
                        self._final_preview_row_pool = self._final_preview_row_pool[-shift:] + self._final_preview_row_pool[:-shift]
                        self._final_preview_row_labels = self._final_preview_row_labels[-shift:] + self._final_preview_row_labels[:-shift]
                        self._final_preview_row_bound_indices = (
                            self._final_preview_row_bound_indices[-shift:] + self._final_preview_row_bound_indices[:-shift]
                        )
                        for slot in range(0, shift):
                            self._bind_final_preview_row(slot, start_idx + slot, images_by_index)
                else:
                    for slot in range(visible_count):
                        page_idx = start_idx + slot
                        if self._final_preview_row_bound_indices[slot] != page_idx:
                            self._bind_final_preview_row(slot, page_idx, images_by_index)
            else:
                for slot in range(visible_count):
                    self._bind_final_preview_row(slot, start_idx + slot, images_by_index)
        else:
            for slot in range(visible_count):
                page_idx = start_idx + slot
                if self._final_preview_row_bound_indices[slot] != page_idx:
                    self._bind_final_preview_row(slot, page_idx, images_by_index)

        self.view.clear_preview_widgets()
        row = 0
        if self._final_preview_pool_top_spacer is not None:
            self.view.add_preview_widget(self._final_preview_pool_top_spacer, row)
            row += 1
        for pooled_row in self._final_preview_row_pool:
            self.view.add_preview_widget(pooled_row, row)
            row += 1
        if self._final_preview_pool_bottom_spacer is not None:
            self.view.add_preview_widget(self._final_preview_pool_bottom_spacer, row)

        self._preview_image_refs = [images_by_index[idx] for idx in range(start_idx, end_idx + 1) if idx in images_by_index]
        self.view.refresh_preview_layout()
        virtual_top = self._virtual_top_from_anchor()
        self._final_preview_syncing_scrollbar = True
        try:
            self.view.preview_canvas.yview_moveto(self._fraction_for_virtual_top(virtual_top))
        finally:
            self._final_preview_syncing_scrollbar = False
        self._trim_preview_caches()

    def _on_final_preview_result(self, result: RenderResult) -> None:
        self.master.after(0, lambda: self._apply_final_preview_result(result))

    def _apply_final_preview_result(self, result: RenderResult) -> None:
        if result.request.generation_id != self._final_preview_generation:
            return

        index = next(
            (
                idx
                for idx, page in enumerate(self._final_preview_pages)
                if page.source_path == result.request.source_path and page.page_index == result.request.page_index
            ),
            None,
        )
        if index is None:
            return

        self._final_preview_pending_indices.discard(index)

        if result.error is not None:
            if index not in self._final_preview_render_errors:
                self._final_preview_render_errors.add(index)
                self.show_preview_text("Could not render this page.\nThe file may be encrypted or corrupt.")
            if not self._final_preview_pending_indices:
                self._final_preview_rendering = False
            return

        image = result.image
        if image is None:
            return

        key = self.preview_service.decoded_cache_key(
            result.request.source_path,
            result.request.page_index,
            result.request.zoom,
        )
        self.preview_service.store_decoded_image(key, image)
        self._final_preview_decoded_by_index[index] = (key, image)

        measured_height = max(image.height, 1)
        descriptor = self._final_preview_pages[index]
        if measured_height != descriptor.estimated_height:
            descriptor.estimated_height = measured_height
            self._recompute_final_preview_offsets()

        self._compose_final_preview_widgets(allow_fast_path=True)
        if not self._final_preview_pending_indices:
            self._final_preview_rendering = False

    def _render_virtual_final_preview(self, preserve_anchor: bool) -> None:
        self._request_final_preview_render(preserve_anchor=preserve_anchor, refresh_generation=True)

    def update_preview(self) -> None:
        current_mode = self.view.preview_mode.get()
        if current_mode != self._last_preview_mode:
            self._last_preview_mode = current_mode
            self._final_preview_images_by_index = {}
            self._final_preview_decoded_by_index = {}
            self._trim_preview_caches()

        if not self.model.sequence:
            self._last_preview_render_key = None
            self.view.preview_caption.configure(text="No pages loaded")
            self._update_zoom_label()
            self._advance_final_preview_generation()
            self._final_preview_images_by_index = {}
            self._final_preview_decoded_by_index = {}
            self._trim_preview_caches()
            self.show_preview_text("Open one or more PDFs to begin.")
            return

        if current_mode == self.view.PREVIEW_SINGLE:
            self._final_preview_pages = []
            self._final_preview_visible_indices = set()
            self._advance_final_preview_generation()
            self._final_preview_images_by_index = {}
            self._final_preview_decoded_by_index = {}
            self._trim_preview_caches()
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
        self._final_preview_render_errors = set()
        self._final_preview_images_by_index = {}
        self._final_preview_decoded_by_index = {}
        self._trim_preview_caches()
        self._request_final_preview_render(preserve_anchor=True, refresh_generation=True)
        self._last_preview_render_key = preview_key
