# ADR 0002: Error strategy and exception translation

- **Status:** Accepted
- **Date:** 2026-03-12

## Context

The application handles failures from PDF loading, preview rendering, and merge export. Error-handling behavior needs a consistent layering strategy so exceptions are actionable in lower layers and user-friendly in the UI.

## Decision

### 1) Exception ownership by layer

- **Domain layer (`src/pdf_merge_gui/domain/`)**
  - Prefer no exceptions beyond basic invariant checks on pure data.
  - Domain objects should remain lightweight and framework/library agnostic.

- **Service/model layers (`src/pdf_merge_gui/services/*.py`, `src/pdf_merge_gui/model.py`)**
  - Raise typed service/domain exceptions for predictable business/use-case failures.
  - Do not emit raw UI concerns (no `messagebox`, no UI text formatting).

- **Adapter/integration boundaries (`src/pdf_merge_gui/adapters/pypdf_adapter.py`, preview integration code)**
  - Catch third-party exceptions (`pypdf`, `fitz`) and wrap/translate into typed application exceptions.

### 2) User-facing translation boundary

- User-facing error messages are translated in the **UI controller boundary**, currently `src/pdf_merge_gui/ui/controller.py`.
- `messagebox.showerror` / `showwarning` / `showinfo` usage stays in UI code.
- UI should map typed lower-layer exceptions to concise, actionable text.

### 3) Wrapping third-party preview exceptions

Use typed wrappers in preview code (`src/pdf_merge_gui/preview.py`) as the pattern:

- `PreviewDependencyUnavailable`
  - Raised when optional preview dependency import fails (e.g., `fitz` / PyMuPDF unavailable).
- `PreviewRenderError`
  - Raised for rendering failures (invalid page index, document/page render issues).

Rules:

1. Catch raw third-party exceptions at the boundary and re-raise typed wrappers.
2. Preserve the original exception as `__cause__` (`raise ... from exc`) for diagnostics.
3. Let UI decide display behavior:
   - dependency issue: non-fatal “preview unavailable” messaging;
   - render issue: error dialog and fallback preview text;
   - unexpected issue: generic user-safe message.

## Consequences

- Lower layers remain testable and deterministic with typed exceptions.
- UI presents consistent messaging while preserving technical causes for debugging.
- Third-party API changes have reduced blast radius because exception translation is centralized.
