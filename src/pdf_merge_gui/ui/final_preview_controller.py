"""Final preview virtualization/render ownership for UI layer.

This module owns final-preview state mapping, scroll-anchor synchronization, and
virtual-window rendering logic. It depends only on UI/controller-facing model
and service interfaces (ui -> model/services), and must not reach adapter internals.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import time
import tkinter as tk
from typing import TYPE_CHECKING

from PIL import ImageTk

if TYPE_CHECKING:
    from .controller import PdfMergeController


@dataclass
class FinalPreviewPage:
    source_path: str
    page_index: int
    rotation_degrees: int
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
        now = time.monotonic()
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
        if (
            owner._final_preview_last_synced_fraction is not None
            and owner._final_preview_last_sync_ts is not None
            and (now - owner._final_preview_last_sync_ts) <= owner.FINAL_SCROLL_SYNC_CALLBACK_SUPPRESSION_WINDOW_S
            and abs(first_fraction - owner._final_preview_last_synced_fraction)
            <= owner.FINAL_SCROLL_SYNC_CALLBACK_SUPPRESSION_EPSILON
        ):
            owner._log_preview_debug(
                f"_on_preview_canvas_yscroll suppressed_post_sync_callback first_fraction={first_fraction:.6f} "
                f"synced_fraction={owner._final_preview_last_synced_fraction:.6f} "
                f"elapsed_ms={(now - owner._final_preview_last_sync_ts) * 1000.0:.2f}"
            )
            return
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

        previous_ts = owner._final_preview_last_scroll_event_ts
        previous_top = owner._final_preview_last_logical_top
        previous_velocity = owner._final_preview_scroll_velocity_px_s
        instantaneous_velocity = previous_velocity
        if previous_ts is not None and previous_top is not None:
            dt_s = now - previous_ts
            if dt_s > 0:
                instantaneous_velocity = abs(logical_top - previous_top) / dt_s
        alpha = owner.FINAL_SCROLL_VELOCITY_EMA_ALPHA
        velocity = (alpha * instantaneous_velocity) + ((1.0 - alpha) * previous_velocity)
        owner._final_preview_last_scroll_event_ts = now
        owner._final_preview_last_logical_top = logical_top
        owner._final_preview_scroll_velocity_px_s = velocity
        owner._log_preview_debug(
            f"_on_preview_canvas_yscroll anchor_updated={owner._final_preview_anchor_fraction:.6f} "
            f"first_fraction={first_fraction:.6f} rendered_top={rendered_top:.2f} "
            f"rendered_max_start={rendered_max_start} logical_top={logical_top:.2f} "
            f"velocity_px_s={velocity:.2f} instantaneous_velocity_px_s={instantaneous_velocity:.2f}"
        )
        self._sync_final_preview_list_selection(logical_top=logical_top, viewport_height=viewport_height)
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
        debounce_ms = owner.compute_debounce_ms(owner._final_preview_scroll_velocity_px_s)
        elapsed_since_last_render_ms = None
        if owner._final_preview_last_scroll_render_ts is not None:
            elapsed_since_last_render_ms = (now - owner._final_preview_last_scroll_render_ts) * 1000.0
        if elapsed_since_last_render_ms is not None and elapsed_since_last_render_ms >= owner.FINAL_SCROLL_RENDER_MAX_UPDATE_INTERVAL_MS:
            debounce_ms = 0
        owner._log_preview_debug(
            f"_on_preview_canvas_yscroll schedule_scroll_render debounce_ms={debounce_ms} "
            f"velocity_px_s={owner._final_preview_scroll_velocity_px_s:.2f} "
            f"max_update_interval_ms={owner.FINAL_SCROLL_RENDER_MAX_UPDATE_INTERVAL_MS} "
            f"elapsed_since_last_render_ms={elapsed_since_last_render_ms}"
        )
        owner._final_preview_last_scroll_render_anchor = owner._final_preview_anchor_fraction
        owner._pending_final_scroll_render_after = owner.master.after(
            debounce_ms,
            self.render_final_preview_from_scroll,
        )

    def render_final_preview_from_scroll(self) -> None:
        owner = self.owner
        owner._pending_final_scroll_render_after = None
        if owner.view.preview_mode.get() != owner.view.PREVIEW_FINAL:
            return
        if owner._final_preview_rendering:
            return
        owner._final_preview_last_scroll_render_ts = time.monotonic()
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
        owner._final_preview_last_synced_fraction = target_fraction
        owner._final_preview_last_sync_ts = time.monotonic()
        try:
            owner.view.preview_canvas.yview_moveto(target_fraction)
            owner._final_preview_last_sync_ts = time.monotonic()
        finally:
            owner._final_preview_syncing_scrollbar = False
        return True

    def build_final_preview_model(self) -> None:
        owner = self.owner
        sequence = [(page.source_path, page.page_index, page.rotation_degrees) for page in owner.model.sequence]
        existing = [(page.source_path, page.page_index, page.rotation_degrees) for page in owner._final_preview_pages]
        if sequence == existing:
            return

        previous_heights = {
            (page.source_path, page.page_index, page.rotation_degrees): page.estimated_height
            for page in owner._final_preview_pages
        }
        owner._final_preview_pages = [
            FinalPreviewPage(
                source_path=source_path,
                page_index=page_index,
                rotation_degrees=rotation_degrees,
                estimated_height=previous_heights.get(
                    (source_path, page_index, rotation_degrees),
                    owner.FINAL_PREVIEW_ESTIMATED_PAGE_HEIGHT,
                ),
            )
            for source_path, page_index, rotation_degrees in sequence
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

        if owner._final_preview_dynamic_overscan_enabled:
            velocity = owner._final_preview_scroll_velocity_px_s
            overscan_pages = owner.compute_overscan_pages(velocity)
            bucket = owner._final_preview_velocity_bucket
        else:
            overscan_pages = owner.FINAL_PREVIEW_OVERSCAN_PAGES
            bucket = "fixed"

        start = max(bisect_right(owner._final_preview_offsets, top) - 1 - overscan_pages, 0)
        end = min(
            bisect_right(owner._final_preview_offsets, bottom) - 1 + overscan_pages,
            len(owner._final_preview_pages) - 1,
        )
        rendered_pages = max(end - start + 1, 0)
        owner._log_preview_debug(
            f"_visible_page_range top={top} bottom={bottom} pages={len(owner._final_preview_pages)} "
            f"velocity_bucket={bucket} overscan={overscan_pages} rendered_pages={rendered_pages} -> start={start} end={end}"
        )
        return start, end

    def _estimate_rendered_block_height(self, start_idx: int, end_idx: int) -> int:
        owner = self.owner
        if end_idx < start_idx:
            return 0
        estimated_pages_height = sum(
            max(owner._final_preview_pages[idx].estimated_height, 1) for idx in range(start_idx, end_idx + 1)
        )
        return estimated_pages_height + owner._grid_inter_widget_padding(end_idx - start_idx + 1)

    def _trim_range_to_canvas_budget(self, start_idx: int, end_idx: int, logical_anchor: int) -> tuple[int, int]:
        owner = self.owner
        safe_canvas_budget = owner._final_preview_safe_canvas_budget()
        trimmed_start = start_idx
        trimmed_end = end_idx
        while trimmed_start <= trimmed_end:
            estimated_block_height = self._estimate_rendered_block_height(trimmed_start, trimmed_end)
            if estimated_block_height <= safe_canvas_budget:
                break
            if trimmed_end - trimmed_start <= 0:
                break
            dist_start = abs(owner._final_preview_offsets[trimmed_start] - logical_anchor)
            dist_end = abs(owner._final_preview_offsets[trimmed_end] - logical_anchor)
            if dist_end >= dist_start:
                trimmed_end -= 1
            else:
                trimmed_start += 1
        return trimmed_start, trimmed_end

    def _schedule_single_reconciliation_render(self) -> None:
        owner = self.owner
        pending = getattr(owner, "_pending_final_reconcile_after", None)
        if pending is not None:
            return

        def _run_reconciliation() -> None:
            owner._pending_final_reconcile_after = None
            if owner.view.preview_mode.get() != owner.view.PREVIEW_FINAL:
                return
            self.render_virtual_final_preview(preserve_anchor=True, _allow_reconciliation=False)

        owner._pending_final_reconcile_after = owner.master.after(0, _run_reconciliation)

    def render_virtual_final_preview(self, preserve_anchor: bool, _allow_reconciliation: bool = True) -> None:
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
            logical_anchor = (top + bottom) // 2
            start_idx, end_idx = self._trim_range_to_canvas_budget(start_idx, end_idx, logical_anchor)
            if end_idx < start_idx:
                return

            requested_indices = set(range(start_idx, end_idx + 1))
            render_signature = self._build_final_render_signature(start_idx, end_idx)
            owner._log_preview_debug(
                f"_render_virtual_final_preview preserve_anchor={preserve_anchor} start_idx={start_idx} end_idx={end_idx} "
                f"requested_count={len(requested_indices)} viewport={owner.view.preview_canvas.winfo_width()}x{owner.view.preview_canvas.winfo_height()}"
            )
            if (
                preserve_anchor
                and requested_indices == owner._final_preview_visible_indices
                and render_signature == getattr(owner, "_last_final_render_signature", None)
            ):
                rendered_fraction = self._rendered_scroll_fraction_for_anchor()
                self.sync_canvas_scroll_to_fraction(rendered_fraction)
                return

            # Test harnesses that construct controllers without full runtime worker state
            # still use synchronous rendering behavior.
            if not hasattr(owner, "render_worker"):
                images_by_index: dict[int, ImageTk.PhotoImage] = {}
                for idx in range(start_idx, end_idx + 1):
                    descriptor = owner._final_preview_pages[idx]
                    rendered = owner.render_preview_image(descriptor.source_path, descriptor.page_index, descriptor.rotation_degrees)
                    if rendered is None:
                        return
                    images_by_index[idx] = rendered
                self._apply_rendered_images(
                    start_idx=start_idx,
                    end_idx=end_idx,
                    images_by_index=images_by_index,
                    preserve_anchor=preserve_anchor,
                    logical_anchor=logical_anchor,
                    _allow_reconciliation=_allow_reconciliation,
                )
                return

            requests = {
                idx: (
                    owner._final_preview_pages[idx].source_path,
                    owner._final_preview_pages[idx].page_index,
                    owner._final_preview_pages[idx].rotation_degrees,
                )
                for idx in range(start_idx, end_idx + 1)
            }
            owner.queue_final_preview_render(
                requests=requests,
                meta={
                    "start_idx": start_idx,
                    "end_idx": end_idx,
                    "preserve_anchor": preserve_anchor,
                    "logical_anchor": logical_anchor,
                    "allow_reconciliation": _allow_reconciliation,
                },
            )
        finally:
            owner._final_preview_rendering = False

    def apply_completed_final_render_generation(self, generation_id: int) -> None:
        owner = self.owner
        if owner.view.preview_mode.get() != owner.view.PREVIEW_FINAL:
            return
        meta = owner.get_final_render_meta(generation_id)
        if not meta:
            return
        images_by_index = owner.get_completed_final_render_images(generation_id)
        start_idx = int(meta["start_idx"])
        end_idx = int(meta["end_idx"])
        preserve_anchor = bool(meta.get("preserve_anchor", True))
        logical_anchor = int(meta.get("logical_anchor", 0))
        allow_reconciliation = bool(meta.get("allow_reconciliation", True))
        self._apply_rendered_images(
            start_idx=start_idx,
            end_idx=end_idx,
            images_by_index=images_by_index,
            preserve_anchor=preserve_anchor,
            logical_anchor=logical_anchor,
            _allow_reconciliation=allow_reconciliation,
        )

    def _apply_rendered_images(
        self,
        *,
        start_idx: int,
        end_idx: int,
        images_by_index: dict[int, ImageTk.PhotoImage],
        preserve_anchor: bool,
        logical_anchor: int,
        _allow_reconciliation: bool,
    ) -> None:
        owner = self.owner
        for idx in range(start_idx, end_idx + 1):
            descriptor = owner._final_preview_pages[idx]
            rendered = images_by_index.get(idx)
            if rendered is None:
                return
            measured_height = max(rendered.height(), 1)
            if measured_height != descriptor.estimated_height:
                descriptor.estimated_height = measured_height

        self.recompute_final_preview_offsets()
        top, bottom = self.visible_virtual_window()
        reconciled_start, reconciled_end = self.visible_page_range(top, bottom)
        reconciled_start, reconciled_end = self._trim_range_to_canvas_budget(
            reconciled_start,
            reconciled_end,
            logical_anchor=(top + bottom) // 2,
        )
        if _allow_reconciliation and (reconciled_start, reconciled_end) != (start_idx, end_idx):
            owner._log_preview_debug(
                f"_render_virtual_final_preview schedule_reconciliation "
                f"rendered_start={start_idx} rendered_end={end_idx} "
                f"reconciled_start={reconciled_start} reconciled_end={reconciled_end}"
            )
            self._schedule_single_reconciliation_render()

        safe_canvas_budget = owner._final_preview_safe_canvas_budget()
        while start_idx <= end_idx:
            rendered_block_height = (
                sum(max(images_by_index[idx].height(), 1) for idx in range(start_idx, end_idx + 1))
                + owner._grid_inter_widget_padding(end_idx - start_idx + 1)
            )
            if rendered_block_height <= safe_canvas_budget:
                break
            if end_idx - start_idx <= 0:
                break
            dist_start = abs(owner._final_preview_offsets[start_idx] - ((top + bottom) // 2))
            dist_end = abs(owner._final_preview_offsets[end_idx] - ((top + bottom) // 2))
            if dist_end >= dist_start:
                end_idx -= 1
            else:
                start_idx += 1

        owner._preview_image_refs = [images_by_index[idx] for idx in range(start_idx, end_idx + 1) if idx in images_by_index]
        owner._final_preview_visible_indices = set(range(start_idx, end_idx + 1))
        owner._last_final_render_signature = self._build_final_render_signature(start_idx, end_idx)

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
        virtual_top, virtual_bottom = self.visible_virtual_window()
        self._sync_final_preview_list_selection(
            logical_top=virtual_top,
            viewport_height=max(virtual_bottom - virtual_top, 1),
        )

    def _build_final_render_signature(self, start_idx: int, end_idx: int) -> tuple[object, ...]:
        owner = self.owner
        visible_indices = tuple(range(start_idx, end_idx + 1))
        rendered_page_identity = tuple(
            (
                owner._final_preview_pages[idx].source_path,
                owner._final_preview_pages[idx].page_index,
                owner._final_preview_pages[idx].rotation_degrees,
            )
            for idx in visible_indices
        )
        fit_preview = bool(owner.view.fit_preview.get())
        preview_zoom = round(getattr(owner, "preview_zoom", owner.DEFAULT_ZOOM), 2)
        panel_size = owner._panel_size() if fit_preview and hasattr(owner, "_panel_size") else None
        zoom_signature: tuple[object, ...] = (
            preview_zoom,
            fit_preview,
            panel_size,
        )
        return (visible_indices, rendered_page_identity, zoom_signature)

    def _sync_final_preview_list_selection(self, logical_top: float, viewport_height: int) -> None:
        owner = self.owner
        if owner.is_final_preview_selection_locked():
            return
        page_idx = self._active_page_index_for_viewport(logical_top, logical_top + max(viewport_height, 1))
        if page_idx is None:
            return
        if not hasattr(owner.view, "page_list"):
            return

        selected = owner.selected_indices()
        if selected == [page_idx]:
            return
        owner.set_selected_indices([page_idx])

    def _active_page_index_for_viewport(self, logical_top: float, logical_bottom: float) -> int | None:
        owner = self.owner
        if not owner._final_preview_pages or len(owner._final_preview_offsets) < 2:
            return None

        clamped_top = max(0.0, logical_top)
        clamped_bottom = max(clamped_top, logical_bottom)
        page_count = len(owner._final_preview_pages)

        # Keep selection on the first page whose top edge is still visible.
        # This avoids switching to the next page too early while the current
        # page header is still on-screen.
        for idx in range(page_count):
            page_top = float(owner._final_preview_offsets[idx])
            if clamped_top <= page_top <= clamped_bottom:
                return idx

        # If no page top is visible (e.g., viewport is inside a very tall page),
        # stay anchored to the page containing the viewport top.
        for idx in range(page_count):
            page_top = float(owner._final_preview_offsets[idx])
            page_bottom = page_top + float(max(owner._final_preview_pages[idx].logical_height, 1))
            if page_top <= clamped_top < page_bottom:
                return idx

        # Fall back to the next page whose top starts below the viewport top.
        for idx in range(page_count):
            page_top = float(owner._final_preview_offsets[idx])
            if page_top >= clamped_top:
                return idx

        return page_count - 1
