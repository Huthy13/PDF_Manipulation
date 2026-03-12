from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from .tooltip import ToolTip


class PdfMergeView(ttk.Frame):
    PREVIEW_SINGLE = "single"
    PREVIEW_FINAL = "final"

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=12)
        self.master.title("PDF Merge GUI")
        self.master.geometry("1150x700")
        self.preview_mode = tk.StringVar(value=self.PREVIEW_SINGLE)
        self._tooltips: list[ToolTip] = []

        self.open_handler: Optional[Callable[[], None]] = None
        self.move_up_handler: Optional[Callable[[], None]] = None
        self.move_down_handler: Optional[Callable[[], None]] = None
        self.remove_handler: Optional[Callable[[], None]] = None
        self.clear_handler: Optional[Callable[[], None]] = None
        self.merge_handler: Optional[Callable[[], None]] = None
        self.prev_handler: Optional[Callable[[], None]] = None
        self.next_handler: Optional[Callable[[], None]] = None
        self.selection_handler: Optional[Callable[[], None]] = None
        self.preview_mode_handler: Optional[Callable[[], None]] = None
        self.zoom_in_handler: Optional[Callable[[], None]] = None
        self.zoom_out_handler: Optional[Callable[[], None]] = None
        self.zoom_reset_handler: Optional[Callable[[], None]] = None
        self.fit_preview_handler: Optional[Callable[[], None]] = None
        self.ctrl_wheel_zoom_handler: Optional[Callable[[int], None]] = None

        self._build_layout()

    def _create_icon_button(
        self,
        parent: ttk.Frame,
        symbol: str,
        tooltip: str,
        command: Callable[[], None] | None,
        row: int,
        column: int,
    ) -> None:
        btn = ttk.Button(parent, text=symbol, command=command, width=8)
        btn.grid(row=row, column=column, sticky="ew", padx=4, pady=4)
        self._tooltips.append(ToolTip(btn, tooltip))

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

        self.btn_open = ttk.Button(controls, text="➕", width=8)
        self.btn_open.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        self._tooltips.append(ToolTip(self.btn_open, "Add PDF pages from one or more documents"))

        self.btn_up = ttk.Button(controls, text="⬆", width=8)
        self.btn_up.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        self._tooltips.append(ToolTip(self.btn_up, "Move selected page(s) up"))

        self.btn_down = ttk.Button(controls, text="⬇", width=8)
        self.btn_down.grid(row=0, column=2, sticky="ew", padx=4, pady=4)
        self._tooltips.append(ToolTip(self.btn_down, "Move selected page(s) down"))

        self.btn_remove = ttk.Button(controls, text="✖", width=8)
        self.btn_remove.grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        self._tooltips.append(ToolTip(self.btn_remove, "Remove selected page(s)"))

        self.btn_clear = ttk.Button(controls, text="➖", width=8)
        self.btn_clear.grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        self._tooltips.append(ToolTip(self.btn_clear, "Clear all pages from the list"))

        self.btn_merge = ttk.Button(controls, text="💾", width=8)
        self.btn_merge.grid(row=1, column=2, sticky="ew", padx=4, pady=4)
        self._tooltips.append(ToolTip(self.btn_merge, "Merge/export the listed pages to a new PDF"))

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

        yscroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.page_list.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.page_list.configure(yscrollcommand=yscroll.set)

        mode_frame = ttk.LabelFrame(right, text="Preview Mode")
        mode_frame.grid(row=0, column=0, sticky="ew")

        self.rb_single = ttk.Radiobutton(
            mode_frame,
            text="Single Page",
            value=self.PREVIEW_SINGLE,
            variable=self.preview_mode,
        )
        self.rb_single.grid(row=0, column=0, sticky="w", padx=8, pady=6)

        self.rb_final = ttk.Radiobutton(
            mode_frame,
            text="Final Output Preview",
            value=self.PREVIEW_FINAL,
            variable=self.preview_mode,
        )
        self.rb_final.grid(row=0, column=1, sticky="w", padx=8, pady=6)

        nav = ttk.Frame(right)
        nav.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        nav.columnconfigure(0, weight=1)
        nav.columnconfigure(1, weight=0)
        nav_inner = ttk.Frame(nav)
        nav_inner.grid(row=0, column=0)

        self.btn_prev = ttk.Button(nav_inner, text="◀ Prev")
        self.btn_prev.grid(row=0, column=0, padx=(0, 12))

        self.preview_caption = ttk.Label(nav_inner, text="No page selected")
        self.preview_caption.grid(row=0, column=1)

        self.btn_next = ttk.Button(nav_inner, text="Next ▶")
        self.btn_next.grid(row=0, column=2, padx=(12, 0))

        zoom_controls = ttk.Frame(nav)
        zoom_controls.grid(row=0, column=1, sticky="e")

        self.btn_zoom_out = ttk.Button(zoom_controls, text="−", width=3)
        self.btn_zoom_out.grid(row=0, column=0, padx=(4, 2))
        self._tooltips.append(ToolTip(self.btn_zoom_out, "Zoom out"))

        self.zoom_label = ttk.Label(zoom_controls, text="100%")
        self.zoom_label.grid(row=0, column=1, padx=2)

        self.btn_zoom_in = ttk.Button(zoom_controls, text="+", width=3)
        self.btn_zoom_in.grid(row=0, column=2, padx=(2, 4))
        self._tooltips.append(ToolTip(self.btn_zoom_in, "Zoom in"))

        self.btn_zoom_reset = ttk.Button(zoom_controls, text="Reset")
        self.btn_zoom_reset.grid(row=0, column=3, padx=(2, 4))
        self._tooltips.append(ToolTip(self.btn_zoom_reset, "Reset zoom to default"))

        self.fit_preview = tk.BooleanVar(value=True)
        self.cb_fit_preview = ttk.Checkbutton(zoom_controls, text="Fit", variable=self.fit_preview)
        self.cb_fit_preview.grid(row=0, column=4)
        self._tooltips.append(ToolTip(self.cb_fit_preview, "Scale preview to available panel size"))

        self.preview_panel = ttk.LabelFrame(right, text="Page Preview")
        self.preview_panel.grid(row=3, column=0, sticky="nsew")
        self.preview_panel.columnconfigure(0, weight=1)
        self.preview_panel.rowconfigure(0, weight=1)

        self.preview_canvas = tk.Canvas(self.preview_panel, highlightthickness=0)
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")

        self.preview_vscroll = ttk.Scrollbar(self.preview_panel, orient=tk.VERTICAL, command=self.preview_canvas.yview)
        self.preview_vscroll.grid(row=0, column=1, sticky="ns")

        self.preview_hscroll = ttk.Scrollbar(self.preview_panel, orient=tk.HORIZONTAL, command=self.preview_canvas.xview)
        self.preview_hscroll.grid(row=1, column=0, sticky="ew")

        self.preview_canvas.configure(
            xscrollcommand=self.preview_hscroll.set,
            yscrollcommand=self.preview_vscroll.set,
        )

        self.preview_label = ttk.Label(
            self.preview_canvas,
            text="Open one or more PDFs to begin.",
            anchor="center",
            justify="center",
            padding=24,
        )
        self.preview_window = self.preview_canvas.create_window(0, 0, anchor="nw", window=self.preview_label)

        self.preview_label.bind("<Configure>", self.on_preview_content_configure)
        self.preview_canvas.bind("<Configure>", self.on_preview_canvas_configure)
        self.preview_canvas.bind("<MouseWheel>", self.on_preview_mousewheel)
        self.preview_canvas.bind("<Shift-MouseWheel>", self.on_preview_shift_mousewheel)
        self.preview_canvas.bind("<Button-4>", self.on_preview_mousewheel)
        self.preview_canvas.bind("<Button-5>", self.on_preview_mousewheel)
        self.preview_canvas.bind("<Shift-Button-4>", self.on_preview_shift_mousewheel)
        self.preview_canvas.bind("<Shift-Button-5>", self.on_preview_shift_mousewheel)
        self.preview_canvas.bind("<Control-MouseWheel>", self.on_preview_ctrl_mousewheel)
        self.preview_canvas.bind("<Control-Button-4>", self.on_preview_ctrl_mousewheel)
        self.preview_canvas.bind("<Control-Button-5>", self.on_preview_ctrl_mousewheel)
        self.preview_label.bind("<MouseWheel>", self.on_preview_mousewheel)
        self.preview_label.bind("<Shift-MouseWheel>", self.on_preview_shift_mousewheel)
        self.preview_label.bind("<Button-4>", self.on_preview_mousewheel)
        self.preview_label.bind("<Button-5>", self.on_preview_mousewheel)
        self.preview_label.bind("<Shift-Button-4>", self.on_preview_shift_mousewheel)
        self.preview_label.bind("<Shift-Button-5>", self.on_preview_shift_mousewheel)
        self.preview_label.bind("<Control-MouseWheel>", self.on_preview_ctrl_mousewheel)
        self.preview_label.bind("<Control-Button-4>", self.on_preview_ctrl_mousewheel)
        self.preview_label.bind("<Control-Button-5>", self.on_preview_ctrl_mousewheel)

    def _reposition_preview_content(self, canvas_width: int, canvas_height: int) -> None:
        content_width = self.preview_label.winfo_reqwidth()
        content_height = self.preview_label.winfo_reqheight()

        x_pos = max((canvas_width - content_width) // 2, 0)
        y_pos = max((canvas_height - content_height) // 2, 0)
        self.preview_canvas.coords(self.preview_window, x_pos, y_pos)
        self.preview_canvas.configure(scrollregion=self.preview_canvas.bbox("all"))

    def on_preview_content_configure(self, _event: tk.Event) -> None:
        canvas_width = max(self.preview_canvas.winfo_width(), 1)
        canvas_height = max(self.preview_canvas.winfo_height(), 1)
        self._reposition_preview_content(canvas_width, canvas_height)

    def on_preview_canvas_configure(self, event: tk.Event) -> None:
        canvas_width = max(event.width, 1)
        canvas_height = max(event.height, 1)
        self._reposition_preview_content(canvas_width, canvas_height)

    def _mousewheel_units(self, event: tk.Event) -> int:
        delta = getattr(event, "delta", 0) or 0
        if delta:
            return int(-delta / 120) or (-1 if delta > 0 else 1)

        num = getattr(event, "num", None)
        if num == 4:
            return -1
        if num == 5:
            return 1
        return 0

    def on_preview_mousewheel(self, event: tk.Event) -> str:
        units = self._mousewheel_units(event)
        if units == 0:
            return "break"
        self.preview_canvas.yview_scroll(units, "units")
        return "break"

    def on_preview_shift_mousewheel(self, event: tk.Event) -> str:
        units = self._mousewheel_units(event)
        if units == 0:
            return "break"
        self.preview_canvas.xview_scroll(units, "units")
        return "break"

    def on_preview_ctrl_mousewheel(self, event: tk.Event) -> str:
        units = self._mousewheel_units(event)
        if units == 0:
            return "break"
        if self.ctrl_wheel_zoom_handler is not None:
            self.ctrl_wheel_zoom_handler(units)
        return "break"

    def reset_preview_scroll(self) -> None:
        self.preview_canvas.xview_moveto(0.0)
        self.preview_canvas.yview_moveto(0.0)

    def bind_handlers(self) -> None:
        self.btn_open.configure(command=self.open_handler)
        self.btn_up.configure(command=self.move_up_handler)
        self.btn_down.configure(command=self.move_down_handler)
        self.btn_remove.configure(command=self.remove_handler)
        self.btn_clear.configure(command=self.clear_handler)
        self.btn_merge.configure(command=self.merge_handler)
        self.btn_prev.configure(command=self.prev_handler)
        self.btn_next.configure(command=self.next_handler)
        self.rb_single.configure(command=self.preview_mode_handler)
        self.rb_final.configure(command=self.preview_mode_handler)
        self.btn_zoom_in.configure(command=self.zoom_in_handler)
        self.btn_zoom_out.configure(command=self.zoom_out_handler)
        self.btn_zoom_reset.configure(command=self.zoom_reset_handler)
        self.cb_fit_preview.configure(command=self.fit_preview_handler)
        self.page_list.bind("<<TreeviewSelect>>", lambda _e: self.selection_handler and self.selection_handler())
