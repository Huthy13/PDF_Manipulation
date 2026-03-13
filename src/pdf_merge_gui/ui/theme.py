from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def apply_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)

    available_themes = style.theme_names()
    base_theme = "clam" if "clam" in available_themes else style.theme_use()
    style.theme_use(base_theme)

    colors = {
        "surface": "#f5f7fb",
        "surface_elevated": "#ffffff",
        "text": "#1f2937",
        "subtle_text": "#6b7280",
        "accent": "#2563eb",
        "danger": "#dc2626",
        "border": "#d1d5db",
    }

    root.configure(bg=colors["surface"])

    style.configure(".", background=colors["surface"], foreground=colors["text"])

    style.configure("TFrame", background=colors["surface"])
    style.configure("TLabel", background=colors["surface"], foreground=colors["text"])
    style.configure(
        "TButton",
        background=colors["surface_elevated"],
        foreground=colors["text"],
        bordercolor=colors["border"],
        lightcolor=colors["surface_elevated"],
        darkcolor=colors["border"],
        relief="flat",
        padding=(10, 6),
    )
    style.map(
        "TButton",
        background=[("pressed", "#e5e7eb"), ("active", "#eef2ff")],
        foreground=[("disabled", colors["subtle_text"])],
    )

    style.configure(
        "TRadiobutton",
        background=colors["surface"],
        foreground=colors["text"],
        indicatorcolor=colors["surface_elevated"],
    )
    style.map(
        "TRadiobutton",
        foreground=[("disabled", colors["subtle_text"])],
        indicatorcolor=[("selected", colors["accent"])],
    )

    style.configure(
        "TCheckbutton",
        background=colors["surface"],
        foreground=colors["text"],
        indicatorcolor=colors["surface_elevated"],
    )
    style.map(
        "TCheckbutton",
        foreground=[("disabled", colors["subtle_text"])],
        indicatorcolor=[("selected", colors["accent"])],
    )

    style.configure(
        "Treeview",
        background=colors["surface_elevated"],
        fieldbackground=colors["surface_elevated"],
        foreground=colors["text"],
        bordercolor=colors["border"],
        rowheight=28,
    )
    style.map(
        "Treeview",
        background=[("selected", colors["accent"])],
        foreground=[("selected", "#ffffff")],
    )
    style.configure(
        "Treeview.Heading",
        background=colors["surface"],
        foreground=colors["text"],
        bordercolor=colors["border"],
        relief="flat",
        padding=(8, 6),
    )
    style.map("Treeview.Heading", background=[("active", "#e5e7eb")])

    style.configure(
        "TLabelframe",
        background=colors["surface"],
        bordercolor=colors["border"],
        relief="solid",
    )
    style.configure(
        "TLabelframe.Label",
        background=colors["surface"],
        foreground=colors["subtle_text"],
    )

    style.configure(
        "Primary.TButton",
        background=colors["accent"],
        foreground="#ffffff",
        bordercolor=colors["accent"],
        lightcolor=colors["accent"],
        darkcolor=colors["accent"],
        relief="flat",
    )
    style.map(
        "Primary.TButton",
        background=[("pressed", "#1d4ed8"), ("active", "#3b82f6")],
        foreground=[("disabled", "#dbeafe")],
    )

    style.configure(
        "Danger.TButton",
        background=colors["danger"],
        foreground="#ffffff",
        bordercolor=colors["danger"],
        lightcolor=colors["danger"],
        darkcolor=colors["danger"],
        relief="flat",
    )
    style.map(
        "Danger.TButton",
        background=[("pressed", "#991b1b"), ("active", "#ef4444")],
        foreground=[("disabled", "#fee2e2")],
    )
