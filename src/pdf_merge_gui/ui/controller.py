from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional, Sequence

from PIL import ImageTk

from ..model import MergeModel
from ..preview import PreviewDependencyUnavailable, PreviewRenderError
from ..services.preview_service import PreviewService
from .view import PdfMergeView


class PdfMergeController:
    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        self.view = PdfMergeView(master)
        self.model = MergeModel()
        self.preview_service = PreviewService(cache_size=120)

        self.final_preview_index = 0
        self.preview_zoom = 1.5
        self.anchor_index: Optional[int] = None
        self.drag_indices: list[int] = []
        self.drag_drop_index: Optional[int] = None
        self.drag_active = False

        self.view.open_handler = self.on_open_pdfs
        self.view.move_up_handler = self.on_move_up
        self.view.move_down_handler = self.on_move_down
        self.view.remove_handler = self.on_remove_selected
        self.view.clear_handler = self.on_clear_all
        self.view.merge_handler = self.on_merge_export
        self.view.prev_handler = self.on_prev_preview
        self.view.next_handler = self.on_next_preview
        self.view.selection_handler = self.on_selection_changed
        self.view.preview_mode_handler = self.update_preview
        self.view.drag_start_handler = self.on_drag_start
        self.view.drag_motion_handler = self.on_drag_motion
        self.view.drag_drop_handler = self.on_drag_drop
        self.view.range_select_handler = self.on_ctrl_range_select
        self.view.bind_handlers()

        self.master.bind("<Delete>", self.on_delete_shortcut)
        self.master.bind("<Control-Up>", self.on_move_up_shortcut)
        self.master.bind("<Control-Down>", self.on_move_down_shortcut)
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

        self.refresh_list()

    def on_close(self) -> None:
        self.preview_service.clear()
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

    def clear_drag_state(self) -> None:
        self.drag_indices = []
        self.drag_drop_index = None
        self.drag_active = False
        self.view.clear_drop_target()
        self.view.set_drag_status("")

    def item_index_at(self, y: int) -> Optional[int]:
        iid = self.view.page_list.identify_row(y)
        if not iid:
            return None
        try:
            return int(iid)
        except ValueError:
            return None

    def on_selection_changed(self) -> None:
        if not self.drag_active:
            indices = self.selected_indices()
            if indices:
                self.anchor_index = indices[0]
        self.update_preview()

    def on_ctrl_range_select(self, event: tk.Event) -> str:
        idx = self.item_index_at(event.y)
        if idx is None:
            return "break"

        if self.anchor_index is None:
            self.anchor_index = idx
            self.set_selected_indices([idx])
            self.update_preview()
            return "break"

        start = min(self.anchor_index, idx)
        end = max(self.anchor_index, idx)
        self.set_selected_indices(list(range(start, end + 1)))
        self.update_preview()
        return "break"

    def on_drag_start(self, event: tk.Event) -> str | None:
        idx = self.item_index_at(event.y)
        if idx is None:
            self.clear_drag_state()
            return None

        selected = self.selected_indices()
        if idx not in selected:
            self.set_selected_indices([idx])
            selected = [idx]
            self.anchor_index = idx

        if selected:
            self.drag_indices = selected
            self.drag_active = True
            self.view.set_drag_status(f"✋ Holding {len(selected)} item(s)")
        return None

    def on_drag_motion(self, event: tk.Event) -> str | None:
        if not self.drag_active or not self.drag_indices:
            return None

        idx = self.item_index_at(event.y)
        if idx is None:
            self.drag_drop_index = len(self.model.sequence)
            self.view.set_drop_target(len(self.model.sequence) - 1)
            self.view.set_drag_status(f"✋ Holding {len(self.drag_indices)} item(s) • drop at end")
            return None

        bbox = self.view.page_list.bbox(str(idx))
        drop_index = idx
        if bbox:
            _, top, _, height = bbox
            if event.y > top + (height / 2):
                drop_index = idx + 1

        self.drag_drop_index = drop_index
        target_marker = min(drop_index, max(len(self.model.sequence) - 1, 0))
        self.view.set_drop_target(target_marker)
        self.view.set_drag_status(f"✋ Holding {len(self.drag_indices)} item(s) • drop before row {drop_index + 1}")
        return None

    def on_drag_drop(self, _event: tk.Event) -> str | None:
        if not self.drag_active or not self.drag_indices or self.drag_drop_index is None:
            self.clear_drag_state()
            return None

        new_indices = self.model.move_to(self.drag_indices, self.drag_drop_index)
        self.refresh_list(select_indices=new_indices)
        self.clear_drag_state()
        return "break"

    def refresh_list(self, select_index: Optional[int] = None, select_indices: Optional[Sequence[int]] = None) -> None:
        self.view.clear_drop_target()
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

        self.final_preview_index = 0
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
            self.final_preview_index = 0
            self.refresh_list()
            return

        select_index = min(first_idx, len(self.model.sequence) - 1)
        self.final_preview_index = min(self.final_preview_index, len(self.model.sequence) - 1)
        self.refresh_list(select_index=select_index)

    def on_delete_shortcut(self, _event: tk.Event) -> str:
        self.on_remove_selected()
        return "break"

    def on_clear_all(self) -> None:
        self.model.clear()
        self.final_preview_index = 0
        self.preview_service.clear()
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
            self.final_preview_index = max(0, self.final_preview_index - 1)
            self.update_preview()
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
            self.final_preview_index = min(len(self.model.sequence) - 1, self.final_preview_index + 1)
            self.update_preview()
            return

        idx = self.selected_index()
        if idx is None:
            idx = 0
        self.set_selected_indices([min(len(self.model.sequence) - 1, idx + 1)])
        self.update_preview()

    def show_preview_text(self, text: str) -> None:
        self.view.preview_label.configure(text=text, image="")
        self.view.preview_label.image = None

    def show_preview_image(self, image: ImageTk.PhotoImage) -> None:
        self.view.preview_label.configure(image=image, text="")
        self.view.preview_label.image = image

    def render_preview_image(self, source_path: str, page_index: int) -> Optional[ImageTk.PhotoImage]:
        try:
            return self.preview_service.render(source_path, page_index, self.preview_zoom)
        except PreviewDependencyUnavailable as exc:
            self.show_preview_text(f"Preview unavailable\n\n{exc}")
            return None
        except PreviewRenderError as exc:
            messagebox.showerror("Preview failed", f"Could not render page preview:\n{exc}")
            self.show_preview_text("Could not render this page.\nThe file may be encrypted or corrupt.")
            return None
        except Exception as exc:
            messagebox.showerror("Preview failed", f"Unexpected preview error:\n{exc}")
            self.show_preview_text("Unexpected error while rendering preview.")
            return None

    def update_preview(self) -> None:
        if not self.model.sequence:
            self.view.preview_caption.configure(text="No pages loaded")
            self.show_preview_text("Open one or more PDFs to begin.")
            return

        if self.view.preview_mode.get() == self.view.PREVIEW_SINGLE:
            idx = self.selected_index()
            if idx is None:
                idx = 0
                self.set_selected_indices([idx])
            page = self.model.sequence[idx]
            self.view.preview_caption.configure(text=f"Single Page ({idx + 1}/{len(self.model.sequence)})")
        else:
            idx = min(self.final_preview_index, len(self.model.sequence) - 1)
            self.final_preview_index = idx
            page = self.model.sequence[idx]
            self.view.preview_caption.configure(text=f"Final Output ({idx + 1}/{len(self.model.sequence)})")

        rendered = self.render_preview_image(page.source_path, page.page_index)
        if rendered is not None:
            self.show_preview_image(rendered)
