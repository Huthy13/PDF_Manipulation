from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional, Sequence

from PIL import ImageTk

from .model import MergeModel
from .preview import PreviewDependencyUnavailable, PreviewRenderError, render_page


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip_window: Optional[tk.Toplevel] = None
        self.widget.bind("<Enter>", self._show)
        self.widget.bind("<Leave>", self._hide)

    def _show(self, _event: tk.Event) -> None:
        if self.tip_window is not None:
            return

        x = self.widget.winfo_rootx() + (self.widget.winfo_width() // 2)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6

        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(
            tw,
            text=self.text,
            padding=(8, 4),
            relief="solid",
            borderwidth=1,
            justify="left",
        )
        label.pack()

    def _hide(self, _event: tk.Event) -> None:
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None


class PdfMergeApp(ttk.Frame):
    PREVIEW_SINGLE = "single"
    PREVIEW_FINAL = "final"

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=12)
        self.master.title("PDF Merge GUI")
        self.master.geometry("1150x700")

        self.model = MergeModel()
        self.preview_mode = tk.StringVar(value=self.PREVIEW_SINGLE)
        self.final_preview_index = 0
        self.preview_zoom = 1.5
        self.preview_image_cache: dict[tuple[str, int, float], ImageTk.PhotoImage] = {}
        self._tooltips: list[ToolTip] = []

        self._build_layout()
        self._refresh_list()

        self.master.bind("<Delete>", self.on_delete_shortcut)
        self.master.bind("<Control-Up>", self.on_move_up_shortcut)
        self.master.bind("<Control-Down>", self.on_move_down_shortcut)

    def _build_layout(self) -> None:
        self.pack(fill=tk.BOTH, expand=True)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.grid(row=0, column=0, sticky="nsew")

        left = ttk.Frame(paned, padding=(0, 0, 10, 0))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        right = ttk.Frame(paned, padding=(10, 0, 0, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)

        paned.add(left, weight=2)
        paned.add(right, weight=3)

        controls = ttk.Frame(left)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for col in range(3):
            controls.columnconfigure(col, weight=1, uniform="list_controls")

        self._create_icon_button(
            controls,
            symbol="➕",
            tooltip="Add PDF pages from one or more documents",
            command=self.on_open_pdfs,
            row=0,
            column=0,
        )
        self._create_icon_button(
            controls,
            symbol="⬆",
            tooltip="Move selected page(s) up",
            command=self.on_move_up,
            row=0,
            column=1,
        )
        self._create_icon_button(
            controls,
            symbol="⬇",
            tooltip="Move selected page(s) down",
            command=self.on_move_down,
            row=0,
            column=2,
        )
        self._create_icon_button(
            controls,
            symbol="✖",
            tooltip="Remove selected page(s)",
            command=self.on_remove_selected,
            row=1,
            column=0,
        )
        self._create_icon_button(
            controls,
            symbol="➖",
            tooltip="Clear all pages from the list",
            command=self.on_clear_all,
            row=1,
            column=1,
        )
        self._create_icon_button(
            controls,
            symbol="💾",
            tooltip="Merge/export the listed pages to a new PDF",
            command=self.on_merge_export,
            row=1,
            column=2,
        )

        list_frame = ttk.Frame(left)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.page_list = ttk.Treeview(
            list_frame,
            columns=("filename", "page"),
            show="headings",
            selectmode="extended",
            height=18,
        )
        self.page_list.heading("filename", text="Filename")
        self.page_list.heading("page", text="Document Page")
        self.page_list.column("filename", anchor="w", width=320, stretch=True)
        self.page_list.column("page", anchor="center", width=130, stretch=False)
        self.page_list.grid(row=0, column=0, sticky="nsew")
        self.page_list.bind("<<TreeviewSelect>>", lambda _e: self.update_preview())

        yscroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.page_list.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.page_list.configure(yscrollcommand=yscroll.set)

        mode_frame = ttk.LabelFrame(right, text="Preview Mode")
        mode_frame.grid(row=0, column=0, sticky="ew")

        ttk.Radiobutton(
            mode_frame,
            text="Single Page",
            value=self.PREVIEW_SINGLE,
            variable=self.preview_mode,
            command=self.update_preview,
        ).grid(row=0, column=0, sticky="w", padx=8, pady=6)

        ttk.Radiobutton(
            mode_frame,
            text="Final Output Preview",
            value=self.PREVIEW_FINAL,
            variable=self.preview_mode,
            command=self.update_preview,
        ).grid(row=0, column=1, sticky="w", padx=8, pady=6)

        nav = ttk.Frame(right)
        nav.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        nav.columnconfigure(0, weight=1)

        nav_inner = ttk.Frame(nav)
        nav_inner.grid(row=0, column=0)

        ttk.Button(nav_inner, text="◀ Prev", command=self.on_prev_preview).grid(
            row=0, column=0, padx=(0, 12)
        )
        self.preview_caption = ttk.Label(nav_inner, text="No page selected")
        self.preview_caption.grid(row=0, column=1)
        ttk.Button(nav_inner, text="Next ▶", command=self.on_next_preview).grid(
            row=0, column=2, padx=(12, 0)
        )

        self.preview_panel = ttk.LabelFrame(right, text="Page Preview")
        self.preview_panel.grid(row=3, column=0, sticky="nsew")
        self.preview_panel.columnconfigure(0, weight=1)
        self.preview_panel.rowconfigure(0, weight=1)

        self.preview_label = ttk.Label(
            self.preview_panel,
            text="Open one or more PDFs to begin.",
            anchor="center",
            justify="center",
            padding=24,
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew")

    def _create_icon_button(
        self,
        parent: ttk.Frame,
        symbol: str,
        tooltip: str,
        command: object,
        row: int,
        column: int,
    ) -> None:
        btn = ttk.Button(parent, text=symbol, command=command, width=8)
        btn.grid(row=row, column=column, sticky="ew", padx=4, pady=4)
        self._tooltips.append(ToolTip(btn, tooltip))

    def _selected_indices(self) -> list[int]:
        selected: list[int] = []
        for iid in self.page_list.selection():
            try:
                selected.append(int(iid))
            except ValueError:
                continue
        return sorted(set(selected))

    def _selected_index(self) -> Optional[int]:
        indices = self._selected_indices()
        if not indices:
            return None
        return indices[0]

    def _set_selected_indices(self, indices: Sequence[int]) -> None:
        valid = [idx for idx in sorted(set(indices)) if 0 <= idx < len(self.model.sequence)]
        if not valid:
            self.page_list.selection_remove(self.page_list.selection())
            return

        iids = [str(idx) for idx in valid]
        self.page_list.selection_set(iids)
        self.page_list.focus(iids[0])

    def _refresh_list(self, select_index: Optional[int] = None, select_indices: Optional[Sequence[int]] = None) -> None:
        for item in self.page_list.get_children():
            self.page_list.delete(item)
        for idx, page in enumerate(self.model.sequence):
            filename = Path(page.source_path).name
            self.page_list.insert("", tk.END, iid=str(idx), values=(filename, page.page_index + 1))

        if select_indices is not None:
            self._set_selected_indices(select_indices)
        elif select_index is not None and 0 <= select_index < len(self.model.sequence):
            self._set_selected_indices([select_index])

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
                messagebox.showerror(
                    "Could not open PDF",
                    f"Failed to load {Path(filepath).name}:\n{exc}",
                )
                continue
            added_any = True

        if not added_any:
            return

        self.final_preview_index = 0
        self._refresh_list(select_index=len(self.model.sequence) - 1)

    def on_move_up(self) -> None:
        indices = self._selected_indices()
        if not indices:
            return
        if len(indices) == 1:
            new_idx = self.model.move_up(indices[0])
            self._refresh_list(select_index=new_idx)
            return

        new_indices = self.model.move_up_many(indices)
        self._refresh_list(select_indices=new_indices)

    def on_move_up_shortcut(self, _event: tk.Event) -> str:
        self.on_move_up()
        return "break"

    def on_move_down(self) -> None:
        indices = self._selected_indices()
        if not indices:
            return
        if len(indices) == 1:
            new_idx = self.model.move_down(indices[0])
            self._refresh_list(select_index=new_idx)
            return

        new_indices = self.model.move_down_many(indices)
        self._refresh_list(select_indices=new_indices)

    def on_move_down_shortcut(self, _event: tk.Event) -> str:
        self.on_move_down()
        return "break"

    def on_remove_selected(self) -> None:
        indices = self._selected_indices()
        if not indices:
            return
        first_idx = indices[0]
        self.model.remove(indices)
        if not self.model.sequence:
            self.final_preview_index = 0
            self._refresh_list()
            return
        select_index = min(first_idx, len(self.model.sequence) - 1)
        self.final_preview_index = min(self.final_preview_index, len(self.model.sequence) - 1)
        self._refresh_list(select_index=select_index)

    def on_delete_shortcut(self, _event: tk.Event) -> str:
        self.on_remove_selected()
        return "break"

    def on_clear_all(self) -> None:
        self.model.clear()
        self.final_preview_index = 0
        self.preview_image_cache.clear()
        self._refresh_list()

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
        if self.preview_mode.get() == self.PREVIEW_FINAL:
            self.final_preview_index = max(0, self.final_preview_index - 1)
            self.update_preview()
            return

        idx = self._selected_index()
        if idx is None:
            idx = 0
        new_idx = max(0, idx - 1)
        self._set_selected_indices([new_idx])
        self.update_preview()

    def on_next_preview(self) -> None:
        if not self.model.sequence:
            return
        if self.preview_mode.get() == self.PREVIEW_FINAL:
            self.final_preview_index = min(len(self.model.sequence) - 1, self.final_preview_index + 1)
            self.update_preview()
            return

        idx = self._selected_index()
        if idx is None:
            idx = 0
        new_idx = min(len(self.model.sequence) - 1, idx + 1)
        self._set_selected_indices([new_idx])
        self.update_preview()

    def _show_preview_text(self, text: str) -> None:
        self.preview_label.configure(text=text, image="")
        self.preview_label.image = None

    def _show_preview_image(self, image: ImageTk.PhotoImage) -> None:
        self.preview_label.configure(image=image, text="")
        self.preview_label.image = image

    def _render_preview_image(self, source_path: str, page_index: int) -> Optional[ImageTk.PhotoImage]:
        cache_key = (source_path, page_index, self.preview_zoom)
        cached = self.preview_image_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            image = render_page(source_path, page_index, zoom=self.preview_zoom)
        except PreviewDependencyUnavailable as exc:
            self._show_preview_text(f"Preview unavailable\n\n{exc}")
            return None
        except PreviewRenderError as exc:
            messagebox.showerror("Preview failed", f"Could not render page preview:\n{exc}")
            self._show_preview_text(
                "Could not render this page.\nThe file may be encrypted or corrupt."
            )
            return None
        except Exception as exc:
            messagebox.showerror("Preview failed", f"Unexpected preview error:\n{exc}")
            self._show_preview_text("Unexpected error while rendering preview.")
            return None

        photo_image = ImageTk.PhotoImage(image)
        self.preview_image_cache[cache_key] = photo_image
        return photo_image

    def update_preview(self) -> None:
        if not self.model.sequence:
            self.preview_caption.configure(text="No pages loaded")
            self._show_preview_text("Open one or more PDFs to begin.")
            return

        if self.preview_mode.get() == self.PREVIEW_SINGLE:
            idx = self._selected_index()
            if idx is None:
                idx = 0
                self._set_selected_indices([idx])
            page = self.model.sequence[idx]
            self.preview_caption.configure(text=f"Single Page ({idx + 1}/{len(self.model.sequence)})")
        else:
            idx = min(self.final_preview_index, len(self.model.sequence) - 1)
            self.final_preview_index = idx
            page = self.model.sequence[idx]
            self.preview_caption.configure(text=f"Final Output ({idx + 1}/{len(self.model.sequence)})")

        rendered = self._render_preview_image(page.source_path, page.page_index)
        if rendered is not None:
            self._show_preview_image(rendered)


def main() -> None:
    root = tk.Tk()
    PdfMergeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
