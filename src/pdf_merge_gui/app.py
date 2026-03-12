from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional, Sequence

from .model import MergeModel


class PdfMergeApp(ttk.Frame):
    PREVIEW_SINGLE = "single"
    PREVIEW_FINAL = "final"

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=12)
        self.master.title("PDF Merge GUI")
        self.master.geometry("1050x650")

        self.model = MergeModel()
        self.preview_mode = tk.StringVar(value=self.PREVIEW_SINGLE)
        self.final_preview_index = 0

        self._build_layout()
        self._refresh_list()

    def _build_layout(self) -> None:
        self.pack(fill=tk.BOTH, expand=True)
        self.columnconfigure(0, weight=2)
        self.columnconfigure(1, weight=3)
        self.rowconfigure(0, weight=1)

        left = ttk.Frame(self, padding=(0, 0, 10, 0))
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        open_btn = ttk.Button(left, text="Open PDFs…", command=self.on_open_pdfs)
        open_btn.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        list_frame = ttk.Frame(left)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.page_list = ttk.Treeview(
            list_frame,
            columns=("item",),
            show="headings",
            selectmode="browse",
            height=18,
        )
        self.page_list.heading("item", text="filename :: page N")
        self.page_list.grid(row=0, column=0, sticky="nsew")
        self.page_list.bind("<<TreeviewSelect>>", lambda _e: self.update_preview())

        yscroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.page_list.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.page_list.configure(yscrollcommand=yscroll.set)

        controls = ttk.Frame(left)
        controls.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for col in range(2):
            controls.columnconfigure(col, weight=1)

        ttk.Button(controls, text="Move Up", command=self.on_move_up).grid(
            row=0, column=0, sticky="ew", padx=(0, 4), pady=(0, 6)
        )
        ttk.Button(controls, text="Move Down", command=self.on_move_down).grid(
            row=0, column=1, sticky="ew", padx=(4, 0), pady=(0, 6)
        )
        ttk.Button(controls, text="Remove Selected", command=self.on_remove_selected).grid(
            row=1, column=0, sticky="ew", padx=(0, 4), pady=(0, 6)
        )
        ttk.Button(controls, text="Clear All", command=self.on_clear_all).grid(
            row=1, column=1, sticky="ew", padx=(4, 0), pady=(0, 6)
        )
        ttk.Button(controls, text="Merge/Export", command=self.on_merge_export).grid(
            row=2, column=0, columnspan=2, sticky="ew"
        )

        right = ttk.Frame(self, padding=(10, 0, 0, 0))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

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
        nav.columnconfigure(1, weight=1)

        ttk.Button(nav, text="◀ Prev", command=self.on_prev_preview).grid(row=0, column=0, sticky="w")
        ttk.Button(nav, text="Next ▶", command=self.on_next_preview).grid(row=0, column=2, sticky="e")

        self.preview_caption = ttk.Label(nav, text="No page selected")
        self.preview_caption.grid(row=0, column=1, sticky="ew")

        self.preview_panel = ttk.LabelFrame(right, text="Page Preview")
        self.preview_panel.grid(row=2, column=0, sticky="nsew")
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

    def _selected_index(self) -> Optional[int]:
        selected = self.page_list.selection()
        if not selected:
            return None
        idx_str = selected[0]
        try:
            return int(idx_str)
        except ValueError:
            return None

    def _refresh_list(self, select_index: Optional[int] = None) -> None:
        for item in self.page_list.get_children():
            self.page_list.delete(item)
        for idx, page in enumerate(self.model.sequence):
            self.page_list.insert("", tk.END, iid=str(idx), values=(page.display_name,))

        if select_index is not None and 0 <= select_index < len(self.model.sequence):
            iid = str(select_index)
            self.page_list.selection_set(iid)
            self.page_list.focus(iid)
        self.update_preview()

    def on_open_pdfs(self) -> None:
        filepaths: Sequence[str] = filedialog.askopenfilenames(
            title="Select PDF files",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if not filepaths:
            return

        for filepath in filepaths:
            self.model.add_pdf(filepath)

        self.final_preview_index = 0
        self._refresh_list(select_index=len(self.model.sequence) - 1)

    def on_move_up(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        new_idx = self.model.move_up(idx)
        self._refresh_list(select_index=new_idx)

    def on_move_down(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        new_idx = self.model.move_down(idx)
        self._refresh_list(select_index=new_idx)

    def on_remove_selected(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        self.model.remove([idx])
        if not self.model.sequence:
            self.final_preview_index = 0
            self._refresh_list()
            return
        select_index = min(idx, len(self.model.sequence) - 1)
        self.final_preview_index = min(self.final_preview_index, len(self.model.sequence) - 1)
        self._refresh_list(select_index=select_index)

    def on_clear_all(self) -> None:
        self.model.clear()
        self.final_preview_index = 0
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

    def on_next_preview(self) -> None:
        if not self.model.sequence:
            return
        if self.preview_mode.get() == self.PREVIEW_FINAL:
            self.final_preview_index = min(len(self.model.sequence) - 1, self.final_preview_index + 1)
            self.update_preview()

    def update_preview(self) -> None:
        if not self.model.sequence:
            self.preview_caption.configure(text="No pages loaded")
            self.preview_label.configure(text="Open one or more PDFs to begin.")
            return

        if self.preview_mode.get() == self.PREVIEW_SINGLE:
            idx = self._selected_index()
            if idx is None:
                idx = 0
                self.page_list.selection_set(str(idx))
            page = self.model.sequence[idx]
            self.preview_caption.configure(text=f"Single Page ({idx + 1}/{len(self.model.sequence)})")
            self.preview_label.configure(
                text=(
                    "Single-page preview\n\n"
                    f"File: {Path(page.source_path).name}\n"
                    f"Page: {page.page_index + 1}"
                )
            )
        else:
            idx = min(self.final_preview_index, len(self.model.sequence) - 1)
            self.final_preview_index = idx
            page = self.model.sequence[idx]
            self.preview_caption.configure(text=f"Final Output ({idx + 1}/{len(self.model.sequence)})")
            self.preview_label.configure(
                text=(
                    "Merged-order preview\n\n"
                    f"Sequence: {idx + 1}\n"
                    f"From: {Path(page.source_path).name}\n"
                    f"Page: {page.page_index + 1}"
                )
            )


def main() -> None:
    root = tk.Tk()
    PdfMergeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
