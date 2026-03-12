from __future__ import annotations

import tkinter as tk

from .ui import PdfMergeController


def main() -> None:
    root = tk.Tk()
    PdfMergeController(root)
    root.mainloop()


if __name__ == "__main__":
    main()
