# PDF Merge GUI

A lightweight desktop GUI for selecting, reordering, previewing, and exporting PDF pages.

## Features (mapped to requirements)

- **R1 – Load source PDFs into a page-level list**: Add one or more PDFs and expand them into individual rows (`filename :: page N`).
- **R2 – Reorder pages before merge**: Move selected rows up/down via buttons or keyboard shortcuts.
- **R3 – Remove pages from output**: Remove selected rows or clear the entire sequence.
- **R4 – Preview pages before exporting**:
  - Single-page preview (selected source row)
  - Final-output preview (walk the merged order)
- **R5 – Export merged PDF**: Write the assembled page sequence to a new output file.
- **R6 – Keyboard productivity shortcuts**:
  - `Delete` = remove selected row
  - `Ctrl+Up` = move selected row up
  - `Ctrl+Down` = move selected row down

## Installation

1. Create and activate a virtual environment (recommended).
2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Run

You can run either entrypoint:

```bash
python run_gui.py
```

or

```bash
python -m pdf_merge_gui.app
```

## Known limitations

- **Very large PDFs** can consume significant memory and may make previews slower.
- **Encrypted/password-protected PDFs** may fail to load or preview unless already decrypted.
- Corrupt or partially unreadable PDF files can trigger load/render/export errors.
