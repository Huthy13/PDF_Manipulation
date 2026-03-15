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

## Architecture

The backend has been split into modular layers to make future feature growth safer:

- `src/pdf_merge_gui/ui/`
  - `view.py`: widget/layout construction
  - `controller.py`: UI event orchestration and interaction behavior
- `src/pdf_merge_gui/services/`
  - `sequence_service.py`: page sequence operations (move/remove/clear)
  - `preview_service.py`: preview rendering + bounded LRU cache
- `src/pdf_merge_gui/adapters/`
  - `pypdf_adapter.py`: PDF read/write adapter and document session reuse
- `src/pdf_merge_gui/model.py`
  - `MergeModel`: orchestration façade that owns the `PdfDocumentSession` lifecycle directly
- `src/pdf_merge_gui/domain/`
  - `models.py`: domain model objects (`PageRef`)
- `src/pdf_merge_gui/utils/`
  - `cache.py`: reusable LRU cache implementation

Ownership note: PDF merge session lifecycle is owned by `MergeModel` and backed directly by
`adapters.pypdf_adapter.PdfDocumentSession` (there is no separate document-session service layer).

## ADR index

- [Architecture Decision Records](docs/adr/)
- [ADR 0001: Layering boundaries](docs/adr/0001-layering-boundaries.md)
- [ADR 0002: Error strategy and exception translation](docs/adr/0002-error-strategy.md)

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

## Tests

```bash
python -m pip install pytest
python -m pytest -q
```

## Build Windows EXE (no Python required for end users)

Use PyInstaller to build a distributable executable:

```powershell
./scripts/build_windows.ps1
```

Optional one-file build:

```powershell
./scripts/build_windows.ps1 -OneFile
```

Artifacts are written under `dist/`.

## Known limitations

- **Very large PDFs** can consume significant memory and may make previews slower.
- **Encrypted/password-protected PDFs** may fail to load or preview unless already decrypted.
- Corrupt or partially unreadable PDF files can trigger load/render/export errors.

## CI backend health workflow

The repository includes `.github/workflows/backend-health.yml` with three jobs on every push/PR:

1. Unit tests via `pytest`.
2. Static checks via `ruff` and `mypy`.
3. Performance smoke checks against fixture PDFs and `tests/perf_baseline.json` with a 20% regression threshold.

### Local commands

```bash
# unit tests
pytest -q

# static checks
ruff check src scripts tests
mypy --ignore-missing-imports --follow-imports=skip src/pdf_merge_gui/model.py src/pdf_merge_gui/services src/pdf_merge_gui/adapters src/pdf_merge_gui/domain scripts/perf_smoke.py tests

# deterministic fixture setup
python tests/fixtures/setup_perf_fixtures.py

# perf smoke check against baseline
python scripts/perf_smoke.py --baseline tests/perf_baseline.json --threshold 0.20

# update perf baseline intentionally
python scripts/perf_smoke.py --baseline tests/perf_baseline.json --threshold 0.20 --update-baseline
```
