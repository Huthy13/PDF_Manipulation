from __future__ import annotations

import logging
import tkinter as tk

from .ui import PdfMergeController


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )


def main() -> None:
    _configure_logging()
    root = tk.Tk()
    PdfMergeController(root)
    root.mainloop()


if __name__ == "__main__":
    main()
