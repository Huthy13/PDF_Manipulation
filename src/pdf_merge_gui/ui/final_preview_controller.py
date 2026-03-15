"""Final preview virtualization/render ownership for UI layer.

This module owns final-preview state mapping, scroll-anchor synchronization, and
virtual-window rendering logic. It depends only on UI/controller-facing model
and service interfaces (ui -> model/services), and must not reach adapter internals.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import tkinter as tk
from typing import TYPE_CHECKING

from PIL import ImageTk

if TYPE_CHECKING:
    from .controller import PdfMergeController


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


class FinalPreviewController:
    def __init__(self, owner: PdfMergeController) -> None:
        self.owner = owner

    def _rendered_scroll_fraction_for_anchor(self) -> float:
        owner = self.owner
        viewport_height = max(owner.view.preview_canvas.winfo_height(), 1)
        mapping = owner._final_preview_render_window
        if mapping is None:
            return max(0.0, min(1.0, owner._final_preview_anchor_fraction))

        max_start = max(owner._final_preview_total_height - viewport_height, 0)
        logical_top = owner._final_preview_anchor_fraction * max_start
        rendered_top = mapping.top_spacer + (logical_top - mapping.logical_start_offset)
        rendered_max_start = max(mapping.content_height - viewport_height, 0)
        rendered_top = max(0.0, min(rendered_top, float(rendered_max_start)))
        if rendered_max_start == 0:
            return 0.0
        return rendered_top / rendered_max_start

    def on_preview_canvas_yscroll(self, first: str, last: str) -> None:
        owner = self.owner
        owner.view.preview_vscroll.set(first, last)
        owner._log_preview_debug(
            f"_on_preview_canvas_yscroll first={first} last={last} anchor_before={owner._final_preview_anchor_fraction:.6f} "
            f"syncing={owner._final_preview_syncing_scrollbar} rendering={owner._final_preview_rendering}"
        )
        if owner.view.preview_mode.get() != owner.view.PREVIEW_FINAL:
            return
        if owner._final_preview_syncing_scrollbar or owner._final_preview_rendering:
            return
        try:
            first_fraction = float(first)
        except ValueError:
            return
        first_fraction = max(0.0, min(1.0, first_fraction))
        viewport_height = max(owner.view.preview_canvas.winfo_height(), 1)
        scrollregion = owner.view.preview_canvas.cget("scrollregion")
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
        mapping = owner._final_preview_render_window
        if mapping is not None:
            rendered_content_height = max(rendered_content_height, mapping.content_height)
        rendered_max_start = max(rendered_content_height - viewport_height, 0)
        rendered_top = first_fraction * rendered_max_start

        previous_anchor = owner._final_preview_anchor_fraction
        if mapping is None:
            owner._final_preview_anchor_fraction = first_fraction
            logical_top = 0.0
        else:
            logical_top = mapping.logical_start_offset + (rendered_top - mapping.top_spacer)
            max_start = max(owner._final_preview_total_height - viewport_height, 0)
            logical_top = max(0.0, min(logical_top, float(max_start)))
            owner._final_preview_anchor_fraction = 0.0 if max_start == 0 else logical_top / max_start
        owner._log_preview_debug(
            f"_on_preview_canvas_yscroll anchor_updated={owner._final_preview_anchor_fraction:.6f} "
            f"first_fraction={first_fraction:.6f} rendered_top={rendered_top:.2f} "
            f"rendered_max_start={rendered_max_start} logical_top={logical_top:.2f}"
        )
        last_scroll_render_anchor = getattr(owner, "_final_preview_last_scroll_render_anchor", previous_anchor)
        anchor_delta_from_last_render = abs(owner._final_preview_anchor_fraction - last_scroll_render_anchor)
        if mapping is not None and anchor_delta_from_last_render < owner.FINAL_SCROLL_RENDER_ANCHOR_EPSILON:
            owner._log_preview_debug(
                f"_on_preview_canvas_yscroll skip_scroll_render "
                f"anchor_delta_from_last_render={anchor_delta_from_last_render:.6f} "
                f"threshold={owner.FINAL_SCROLL_RENDER_ANCHOR_EPSILON:.6f} "
                f"previous_anchor={previous_anchor:.6f}"
            )
            return
        if owner._pending_final_scroll_render_after is not None:
            return
        owner._final_preview_last_scroll_render_anchor = owner._final_preview_anchor_fraction
        owner._pending_final_scroll_render_after = owner.master.after(
            owner.FINAL_SCROLL_RENDER_DEBOUNCE_MS,
            self.render_final_preview_from_scroll,
        )

    def render_final_preview_from_scroll(self) -> None:
        owner = self.owner
        owner._pending_final_scroll_render_after = None
        if owner.view.preview_mode.get() != owner.view.PREVIEW_FINAL:
            return
        if owner._final_preview_rendering:
            return
        self.render_virtual_final_preview(preserve_anchor=True)

    def sync_canvas_scroll_to_fraction(self, fraction: float) -> bool:
        owner = self.owner
        target_fraction = max(0.0, min(1.0, fraction))
        current_view_getter = getattr(owner.view.preview_canvas, "yview", None)
        if callable(current_view_getter):
            current_view = current_view_getter()
            if current_view:
                current_fraction = current_view[0]
                if abs(current_fraction - target_fraction) < owner.FINAL_SCROLL_SYNC_EPSILON:
                    return False
        owner._final_preview_syncing_scrollbar = True
        try:
            owner.view.preview_canvas.yview_moveto(target_fraction)
        finally:
            owner._final_preview_syncing_scrollbar = False
        return True

    def build_final_preview_model(self) -> None:
        owner = self.owner
        sequence = [(page.source_path, page.page_index) for page in owner.model.sequence]
        existing = [(page.source_path, page.page_index) for page in owner._final_preview_pages]
        if sequence == existing:
            return

        previous_heights = {
            (page.source_path, page.page_index): page.estimated_height
            for page in owner._final_preview_pages
        }
        owner._final_preview_pages = [
            FinalPreviewPage(
                source_path=source_path,
                page_index=page_index,
                estimated_height=previous_heights.get(
                    (source_path, page_index),
                    owner.FINAL_PREVIEW_ESTIMATED_PAGE_HEIGHT,
                ),
            )
            for source_path, page_index in sequence
        ]
        self.recompute_final_preview_offsets()

    def recompute_final_preview_offsets(self) -> None:
        owner = self.owner
        if not owner._final_preview_pages:
            owner._final_preview_offsets = [0]
            owner._final_preview_total_height = 0
            return

        estimated_total = sum(max(page.estimated_height, 1) for page in owner._final_preview_pages)
        safe_scroll_height = owner._final_preview_safe_scroll_height()
        available_height = safe_scroll_height - (len(owner._final_preview_pages) * owner.FINAL_PREVIEW_PAGE_GAP)
        scale = 1.0 if estimated_total <= max(available_height, 1) else max(available_height, 1) / estimated_total

        offsets = [0]
        running = 0
        for page in owner._final_preview_pages:
            page.logical_height = max(int(page.estimated_height * scale), 1)
            running += page.logical_height + owner.FINAL_PREVIEW_PAGE_GAP
            offsets.append(running)
        owner._final_preview_offsets = offsets
        owner._final_preview_total_height = running
        max_spacer = 0
        if len(offsets) > 1:
            max_spacer = max(
                owner._final_preview_offsets[0],
                max(
                    owner._final_preview_offsets[idx + 1] - owner._final_preview_offsets[idx]
                    for idx in range(len(owner._final_preview_pages))
                ),
            )
        owner._log_preview_debug(
            f"_recompute_final_preview_offsets estimated_total={estimated_total} scale={scale:.6f} "
            f"total_height={owner._final_preview_total_height} safe_height={safe_scroll_height} "
            f"max_logical_span={max_spacer}"
        )

    def visible_virtual_window(self) -> tuple[int, int]:
        owner = self.owner
        viewport_height = max(owner.view.preview_canvas.winfo_height(), 1)
        max_start = max(owner._final_preview_total_height - viewport_height, 0)
        virtual_top = int(owner._final_preview_anchor_fraction * max_start)
        window = (virtual_top, virtual_top + viewport_height)
        owner._log_preview_debug(
            f"_visible_virtual_window anchor={owner._final_preview_anchor_fraction:.6f} viewport_height={viewport_height} "
            f"max_start={max_start} top={window[0]} bottom={window[1]}"
        )
        return window

    def visible_page_range(self, top: int, bottom: int) -> tuple[int, int]:
        owner = self.owner
        if not owner._final_preview_pages:
            owner._log_preview_debug(f"_visible_page_range top={top} bottom={bottom} pages=0 -> start=0 end=-1")
            return 0, -1
        start = max(bisect_right(owner._final_preview_offsets, top) - 1 - owner.FINAL_PREVIEW_OVERSCAN_PAGES, 0)
        end = min(
            bisect_right(owner._final_preview_offsets, bottom) - 1 + owner.FINAL_PREVIEW_OVERSCAN_PAGES,
            len(owner._final_preview_pages) - 1,
        )
        owner._log_preview_debug(
            f"_visible_page_range top={top} bottom={bottom} pages={len(owner._final_preview_pages)} -> start={start} end={end}"
        )
        return start, end

    def render_virtual_final_preview(self, preserve_anchor: bool) -> None:
        owner = self.owner
        if owner._final_preview_rendering:
            return
        owner._final_preview_rendering = True
        try:
            if not owner._final_preview_pages:
                owner._final_preview_render_window = None
                owner.show_preview_text("Open one or more PDFs to begin.")
                return
            if not preserve_anchor:
                owner._set_virtual_anchor(0)

            top, bottom = self.visible_virtual_window()
            start_idx, end_idx = self.visible_page_range(top, bottom)
            if end_idx < start_idx:
                return

            requested_indices = set(range(start_idx, end_idx + 1))
            owner._log_preview_debug(
                f"_render_virtual_final_preview preserve_anchor={preserve_anchor} start_idx={start_idx} end_idx={end_idx} "
                f"requested_count={len(requested_indices)} viewport={owner.view.preview_canvas.winfo_width()}x{owner.view.preview_canvas.winfo_height()}"
            )
            if preserve_anchor and requested_indices == owner._final_preview_visible_indices:
                rendered_fraction = self._rendered_scroll_fraction_for_anchor()
                self.sync_canvas_scroll_to_fraction(rendered_fraction)
                return

            images_by_index: dict[int, ImageTk.PhotoImage] = {}
            for idx in range(start_idx, end_idx + 1):
                descriptor = owner._final_preview_pages[idx]
                rendered = owner.render_preview_image(descriptor.source_path, descriptor.page_index)
                if rendered is None:
                    return
                images_by_index[idx] = rendered
                measured_height = max(rendered.height(), 1)
                if measured_height != descriptor.estimated_height:
                    descriptor.estimated_height = measured_height

            self.recompute_final_preview_offsets()
            top, bottom = self.visible_virtual_window()
            start_idx, end_idx = self.visible_page_range(top, bottom)

            safe_canvas_budget = owner._final_preview_safe_canvas_budget()
            while start_idx <= end_idx:
                rendered_block_height = (
                    sum(max(images_by_index[idx].height(), 1) for idx in range(start_idx, end_idx + 1) if idx in images_by_index)
                    + owner._grid_inter_widget_padding(end_idx - start_idx + 1)
                )
                if rendered_block_height <= safe_canvas_budget:
                    break
                if end_idx - start_idx <= 0:
                    break
                logical_anchor = (top + bottom) // 2
                dist_start = abs(owner._final_preview_offsets[start_idx] - logical_anchor)
                dist_end = abs(owner._final_preview_offsets[end_idx] - logical_anchor)
                if dist_end >= dist_start:
                    end_idx -= 1
                else:
                    start_idx += 1

            owner._preview_image_refs = [images_by_index[idx] for idx in range(start_idx, end_idx + 1) if idx in images_by_index]
            owner._final_preview_visible_indices = set(range(start_idx, end_idx + 1))

            for idx in range(start_idx, end_idx + 1):
                if idx in images_by_index:
                    continue
                descriptor = owner._final_preview_pages[idx]
                rendered = owner.render_preview_image(descriptor.source_path, descriptor.page_index)
                if rendered is None:
                    return
                images_by_index[idx] = rendered

            rendered_block_height = (
                sum(max(images_by_index[idx].height(), 1) for idx in range(start_idx, end_idx + 1))
                + owner._grid_inter_widget_padding(end_idx - start_idx + 1)
            )
            top_spacer = owner._final_preview_offsets[start_idx]
            bottom_spacer = max(owner._final_preview_offsets[-1] - owner._final_preview_offsets[end_idx + 1], 0)

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
            logical_start_offset = owner._final_preview_offsets[start_idx]
            if clamped:
                owner._log_preview_debug(
                    f"_render_virtual_final_preview clamped start_idx={start_idx} end_idx={end_idx} "
                    f"rendered_block_height={rendered_block_height} top_spacer={top_spacer} "
                    f"bottom_spacer={bottom_spacer} content_height={content_height}"
                )

            owner._final_preview_render_window = FinalPreviewRenderWindow(
                render_start_idx=start_idx,
                render_end_idx=end_idx,
                logical_start_offset=logical_start_offset,
                top_spacer=top_spacer,
                bottom_spacer=bottom_spacer,
                rendered_block_height=rendered_block_height,
                content_height=content_height,
            )

            top_chunks = len(owner._build_spacer_widgets(top_spacer))
            bottom_chunks = len(owner._build_spacer_widgets(bottom_spacer))
            owner._log_preview_debug(
                f"_render_virtual_final_preview spacer_stats top={top_spacer} bottom={bottom_spacer} "
                f"top_chunks={top_chunks} bottom_chunks={bottom_chunks} rendered_block_height={rendered_block_height} "
                f"safe_canvas_budget={safe_canvas_budget} content_height={content_height}"
            )

            def build() -> list[tk.Widget]:
                widgets: list[tk.Widget] = []
                widgets.extend(owner._build_spacer_widgets(top_spacer))
                for idx in range(start_idx, end_idx + 1):
                    image = images_by_index.get(idx)
                    if image is None:
                        continue
                    preview = tk.Label(owner.view.preview_content, image=image, bd=0, highlightthickness=0)
                    preview.image = image
                    widgets.append(preview)
                widgets.extend(owner._build_spacer_widgets(bottom_spacer))
                return widgets

            owner._show_preview_widgets(build, reset_scroll=not preserve_anchor)
            rendered_fraction = self._rendered_scroll_fraction_for_anchor()
            self.sync_canvas_scroll_to_fraction(rendered_fraction)
        finally:
            owner._final_preview_rendering = False
