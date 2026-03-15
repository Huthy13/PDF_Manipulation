from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from .tooltip import ToolTip


class PdfMergeView(ttk.Frame):
    PREVIEW_SINGLE = "single"
    PREVIEW_FINAL = "final"
    INSERT_HINT_IID = "__insert_hint__"

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
        self.reverse_selected_handler: Optional[Callable[[], None]] = None
        self.reverse_all_handler: Optional[Callable[[], None]] = None
        self.merge_handler: Optional[Callable[[], None]] = None
        self.prev_handler: Optional[Callable[[], None]] = None
        self.next_handler: Optional[Callable[[], None]] = None
        self.selection_handler: Optional[Callable[[], None]] = None
        self.preview_mode_handler: Optional[Callable[[], None]] = None
        self.zoom_in_handler: Optional[Callable[[], None]] = None
        self.zoom_out_handler: Optional[Callable[[], None]] = None
        self.zoom_reset_handler: Optional[Callable[[], None]] = None
        self.fit_preview_handler: Optional[Callable[[], None]] = None
        self.preview_debug_logging_handler: Optional[Callable[[], None]] = None
        self.ctrl_wheel_zoom_handler: Optional[Callable[[int], None]] = None
        self.list_drag_drop_handler: Optional[Callable[[list[int], int], None]] = None
        self.list_ctrl_range_handler: Optional[Callable[[int, int], None]] = None
        self._list_selection_anchor_iid: Optional[str] = None
        self._list_drag_source_iids: list[str] = []
        self._list_drag_pending_iids: list[str] = []
        self._list_drag_start_y: Optional[int] = None
        self._list_drag_preview_index: Optional[int] = None
        self._list_drag_click_candidate_iid: Optional[str] = None
        self._drag_ghost: Optional[tk.Label] = None

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

        self.btn_reverse_selected = ttk.Button(controls, text="⟲ Sel", width=8)
        self.btn_reverse_selected.grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        self._tooltips.append(ToolTip(self.btn_reverse_selected, "Reverse selected page order"))

        self.btn_reverse_all = ttk.Button(controls, text="⟲ All", width=8)
        self.btn_reverse_all.grid(row=1, column=2, sticky="ew", padx=4, pady=4)
        self._tooltips.append(ToolTip(self.btn_reverse_all, "Reverse entire page order"))

        self.btn_clear = ttk.Button(controls, text="➖", width=8)
        self.btn_clear.grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        self._tooltips.append(ToolTip(self.btn_clear, "Clear all pages from the list"))

        self.btn_merge = ttk.Button(controls, text="💾", width=8)
        self.btn_merge.grid(row=2, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
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

        self.preview_debug_logging = tk.BooleanVar(value=False)
        self.cb_preview_debug_logging = ttk.Checkbutton(zoom_controls, text="Debug Log", variable=self.preview_debug_logging)
        self.cb_preview_debug_logging.grid(row=0, column=5, padx=(6, 0))
        self._tooltips.append(ToolTip(self.cb_preview_debug_logging, "Enable preview virtualization debug logging"))

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

        self.preview_content = ttk.Frame(self.preview_canvas, padding=12)
        self.preview_content.columnconfigure(0, weight=1)
        self.preview_window = self.preview_canvas.create_window(0, 0, anchor="nw", window=self.preview_content)

        self.preview_placeholder = ttk.Label(
            self.preview_content,
            text="Open one or more PDFs to begin.",
            anchor="center",
            justify="center",
            padding=24,
        )
        self.preview_placeholder.grid(row=0, column=0, sticky="nsew")

        self.preview_content.bind("<Configure>", self.on_preview_content_configure)
        self.preview_canvas.bind("<Configure>", self.on_preview_canvas_configure)
        self._bind_preview_wheel(self.preview_canvas)
        self._bind_preview_wheel(self.preview_content)
        self._bind_preview_wheel(self.preview_placeholder)

    def _bind_preview_wheel(self, widget: tk.Widget) -> None:
        widget.bind("<MouseWheel>", self.on_preview_mousewheel)
        widget.bind("<Shift-MouseWheel>", self.on_preview_shift_mousewheel)
        widget.bind("<Button-4>", self.on_preview_mousewheel)
        widget.bind("<Button-5>", self.on_preview_mousewheel)
        widget.bind("<Shift-Button-4>", self.on_preview_shift_mousewheel)
        widget.bind("<Shift-Button-5>", self.on_preview_shift_mousewheel)
        widget.bind("<Control-MouseWheel>", self.on_preview_ctrl_mousewheel)
        widget.bind("<Control-Button-4>", self.on_preview_ctrl_mousewheel)
        widget.bind("<Control-Button-5>", self.on_preview_ctrl_mousewheel)

    def clear_preview_widgets(self) -> None:
        for widget in self.preview_content.winfo_children():
            widget.destroy()

    def add_preview_widget(self, widget: tk.Widget, row: int) -> None:
        self._bind_preview_wheel(widget)
        widget.grid(row=row, column=0, pady=6)

    def refresh_preview_layout(self) -> None:
        self.preview_content.update_idletasks()
        canvas_width = max(self.preview_canvas.winfo_width(), 1)
        canvas_height = max(self.preview_canvas.winfo_height(), 1)
        self._reposition_preview_content(canvas_width, canvas_height)

    def _reposition_preview_content(self, canvas_width: int, canvas_height: int) -> None:
        content_width = max(self.preview_content.winfo_reqwidth(), 1)
        content_height = max(self.preview_content.winfo_reqheight(), 1)

        x_pos = max((canvas_width - content_width) // 2, 0)
        y_pos = max((canvas_height - content_height) // 2, 0)
        self.preview_canvas.coords(self.preview_window, x_pos, y_pos)

        region_width = max(content_width + (2 * x_pos), canvas_width)
        region_height = max(content_height + (2 * y_pos), canvas_height)
        self.preview_canvas.configure(scrollregion=(0, 0, region_width, region_height))

    def on_preview_content_configure(self, _event: tk.Event) -> None:
        canvas_width = max(self.preview_canvas.winfo_width(), 1)
        canvas_height = max(self.preview_canvas.winfo_height(), 1)
        self._reposition_preview_content(canvas_width, canvas_height)

    def on_preview_canvas_configure(self, event: tk.Event) -> None:
        canvas_width = max(event.width, 1)
        canvas_height = max(event.height, 1)
        self._reposition_preview_content(canvas_width, canvas_height)

    def _mousewheel_units(self, event: tk.Event) -> int:
        # Tk wheel delta scaling differs by OS/input device (mouse notches,
        # precision trackpads, etc.), so clamp to a small unit range to keep
        # canvas scrolling predictable and avoid huge jumps from large deltas.
        num = getattr(event, "num", None)
        if num == 4:
            return -1
        if num == 5:
            return 1

        delta = getattr(event, "delta", 0) or 0
        if delta:
            direction = -1 if delta > 0 else 1
            magnitude = max(1, abs(int(delta)) // 120)
            return direction * min(magnitude, 3)
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

    def set_list_selection_anchor(self, index: int) -> None:
        self._list_selection_anchor_iid = str(index)

    def on_list_drag_start(self, event: tk.Event) -> str | None:
        self._clear_drag_visuals()
        self._list_drag_source_iids = []
        self._list_drag_preview_index = None
        self._list_drag_pending_iids = []
        self._list_drag_start_y = event.y
        self._list_drag_click_candidate_iid = None

        clicked_iid = self.page_list.identify_row(event.y) or None
        if clicked_iid is None:
            return None

        ctrl_pressed = bool(event.state & 0x0004)
        shift_pressed = bool(event.state & 0x0001)
        if ctrl_pressed and self.list_ctrl_range_handler is not None:
            anchor_iid = self._list_selection_anchor_iid or clicked_iid
            try:
                anchor_index = int(anchor_iid)
                clicked_index = int(clicked_iid)
            except ValueError:
                return None
            self.list_ctrl_range_handler(anchor_index, clicked_index)
            return "break"

        if not shift_pressed:
            self._list_selection_anchor_iid = clicked_iid

        # Preserve multiselect gestures and avoid accidental drags while selecting.
        if shift_pressed:
            return None

        selected_iids = [iid for iid in self.page_list.selection() if iid in self.page_list.get_children()]
        if clicked_iid in selected_iids and len(selected_iids) > 1:
            pending_iids = selected_iids
            self._list_drag_click_candidate_iid = clicked_iid
            self._list_drag_pending_iids = pending_iids
            # Keep multi-selection highlighted until we know if this is a drag.
            return "break"

        self._list_drag_pending_iids = [clicked_iid]
        return None

    def on_list_drag_motion(self, event: tk.Event) -> None:
        # Remove transient insert hint before hit-testing; otherwise the temporary
        # row shifts subsequent rows down and skews insertion math while dragging.
        if self.INSERT_HINT_IID in self.page_list.get_children():
            self.page_list.delete(self.INSERT_HINT_IID)

        if not self._list_drag_source_iids:
            if not self._list_drag_pending_iids or self._list_drag_start_y is None:
                return
            if abs(event.y - self._list_drag_start_y) < 4:
                return
            self._list_drag_source_iids = list(self._list_drag_pending_iids)
            self._list_drag_pending_iids = []
            self._show_drag_ghost(event.x, event.y)
            for iid in self._list_drag_source_iids:
                self.page_list.item(iid, tags=("drag_source",))

        self._move_drag_ghost(event.x, event.y)
        source_iids = set(self._list_drag_source_iids)
        siblings = [
            iid
            for iid in self.page_list.get_children()
            if iid not in source_iids and iid != self.INSERT_HINT_IID
        ]
        if not siblings:
            return

        target_iid = self.page_list.identify_row(event.y)
        if target_iid in source_iids:
            target_iid = None

        if target_iid and target_iid in siblings:
            # Keep drag placement committed to one side (before the hovered row)
            # to avoid midpoint jitter/dead zones while moving.
            target_index = siblings.index(target_iid)
        else:
            row_boxes = [(iid, self.page_list.bbox(iid)) for iid in siblings]
            row_boxes = [(iid, bbox) for iid, bbox in row_boxes if bbox]
            if not row_boxes:
                return

            first_bbox = row_boxes[0][1]
            if event.y < first_bbox[1]:
                target_index = 0
            else:
                target_index = len(siblings)
                for idx, (_iid, bbox) in enumerate(row_boxes):
                    row_top = bbox[1]
                    row_height = bbox[3]
                    row_bottom = row_top + row_height
                    if row_top <= event.y < row_bottom:
                        target_index = idx
                        break
                    if idx + 1 < len(row_boxes):
                        next_top = row_boxes[idx + 1][1][1]
                        if row_bottom <= event.y < next_top:
                            target_index = idx + 1
                            break

        full_children = [iid for iid in self.page_list.get_children() if iid != self.INSERT_HINT_IID]
        source_positions = [full_children.index(iid) for iid in self._list_drag_source_iids if iid in full_children]
        if not source_positions:
            self._list_drag_preview_index = None
            return

        first_source_pos = min(source_positions)
        current_compact_index = sum(1 for iid in full_children[:first_source_pos] if iid not in source_iids)

        if target_index == current_compact_index:
            self._list_drag_preview_index = None
            return

        self._list_drag_preview_index = target_index
        self._show_insert_hint(target_index, siblings)

    def on_list_drag_release(self, _event: tk.Event) -> None:
        self._list_drag_pending_iids = []
        self._list_drag_start_y = None
        click_candidate = self._list_drag_click_candidate_iid
        self._list_drag_click_candidate_iid = None
        self._clear_drag_visuals()

        if not self._list_drag_source_iids:
            if click_candidate is not None and click_candidate in self.page_list.get_children():
                self.page_list.selection_set((click_candidate,))
                self.page_list.focus(click_candidate)
                if self.selection_handler is not None:
                    self.selection_handler()
                return
            return

        source_indices: list[int] = []
        for source_iid in self._list_drag_source_iids:
            try:
                source_indices.append(int(source_iid))
            except ValueError:
                continue

        self._list_drag_source_iids = []

        if not source_indices:
            self._list_drag_preview_index = None
            return

        preview_idx = self._list_drag_preview_index
        self._list_drag_preview_index = None
        if preview_idx is None:
            return

        if self.list_drag_drop_handler is not None:
            self.list_drag_drop_handler(sorted(set(source_indices)), preview_idx)

    def _show_drag_ghost(self, x: int, y: int) -> None:
        count = len(self._list_drag_source_iids)
        self._drag_ghost = tk.Label(
            self.page_list,
            text=f"📄 Moving {count} item{'s' if count != 1 else ''}",
            bg="#202020",
            fg="white",
            padx=8,
            pady=3,
        )
        self._drag_ghost.place(x=x + 14, y=y + 14)

    def _move_drag_ghost(self, x: int, y: int) -> None:
        if self._drag_ghost is not None:
            self._drag_ghost.place(x=x + 14, y=y + 14)

    def _show_insert_hint(self, target_index: int, siblings: list[str]) -> None:
        if self.INSERT_HINT_IID in self.page_list.get_children():
            self.page_list.delete(self.INSERT_HINT_IID)

        # `target_index` is computed against the compact list (`siblings`) that
        # excludes dragged rows. Convert that index back to the visible Treeview
        # index (which still includes dragged rows) so the hint sits exactly
        # where the drop will occur.
        full_children = [iid for iid in self.page_list.get_children() if iid != self.INSERT_HINT_IID]
        bounded_index = max(0, min(target_index, len(siblings)))

        if not siblings:
            insertion_index = len(full_children)
        elif bounded_index >= len(siblings):
            insertion_index = full_children.index(siblings[-1]) + 1
        else:
            insertion_index = full_children.index(siblings[bounded_index])

        self.page_list.insert(
            "",
            insertion_index,
            iid=self.INSERT_HINT_IID,
            values=("Insert Here", ""),
            tags=("insert_hint",),
        )

    def _clear_drag_visuals(self) -> None:
        if self._drag_ghost is not None:
            self._drag_ghost.destroy()
            self._drag_ghost = None
        if self.INSERT_HINT_IID in self.page_list.get_children():
            self.page_list.delete(self.INSERT_HINT_IID)
        for iid in self._list_drag_source_iids:
            if iid in self.page_list.get_children():
                self.page_list.item(iid, tags=())

    def bind_handlers(self) -> None:
        self.btn_open.configure(command=self.open_handler)
        self.btn_up.configure(command=self.move_up_handler)
        self.btn_down.configure(command=self.move_down_handler)
        self.btn_remove.configure(command=self.remove_handler)
        self.btn_clear.configure(command=self.clear_handler)
        self.btn_reverse_selected.configure(command=self.reverse_selected_handler)
        self.btn_reverse_all.configure(command=self.reverse_all_handler)
        self.btn_merge.configure(command=self.merge_handler)
        self.btn_prev.configure(command=self.prev_handler)
        self.btn_next.configure(command=self.next_handler)
        self.rb_single.configure(command=self.preview_mode_handler)
        self.rb_final.configure(command=self.preview_mode_handler)
        self.btn_zoom_in.configure(command=self.zoom_in_handler)
        self.btn_zoom_out.configure(command=self.zoom_out_handler)
        self.btn_zoom_reset.configure(command=self.zoom_reset_handler)
        self.cb_fit_preview.configure(command=self.fit_preview_handler)
        self.cb_preview_debug_logging.configure(command=self.preview_debug_logging_handler)
        self.page_list.bind("<<TreeviewSelect>>", lambda _e: self.selection_handler and self.selection_handler())
        self.page_list.tag_configure("drag_source", background="#D6E4FF")
        self.page_list.tag_configure("insert_hint", background="#CFE8FF", foreground="#0B3D91")
        self.page_list.bind("<ButtonPress-1>", self.on_list_drag_start, add="+")
        self.page_list.bind("<B1-Motion>", self.on_list_drag_motion, add="+")
        self.page_list.bind("<ButtonRelease-1>", self.on_list_drag_release, add="+")
