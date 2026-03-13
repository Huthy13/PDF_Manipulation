from __future__ import annotations

import tkinter as tk

from .ui import PdfMergeController
from .ui.theme import apply_theme


def main() -> None:
    root = tk.Tk()
    apply_theme(root)
    PdfMergeController(root)
    root.mainloop()


if __name__ == "__main__":
    main()
