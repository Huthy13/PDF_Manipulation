# ADR 0001: Layering boundaries

- **Status:** Accepted
- **Date:** 2026-03-12

## Context

This project has a layered structure, but module-level dependency rules are not explicitly documented. Without clear boundaries, UI and orchestration code can drift into adapter details, making testing and future refactors harder.

To remove ambiguity, this ADR references concrete modules in the current repository:

- `src/pdf_merge_gui/ui/controller.py`
- `src/pdf_merge_gui/model.py`
- `src/pdf_merge_gui/services/*.py`
- `src/pdf_merge_gui/adapters/pypdf_adapter.py`

## Decision

The following dependency directions are allowed:

1. **`ui` layer** (`src/pdf_merge_gui/ui/`) may call **model/services APIs** but must not call adapter internals directly.
   - `src/pdf_merge_gui/ui/controller.py` is an orchestration entrypoint and should remain free of direct imports from `src/pdf_merge_gui/adapters/`.
2. **`services` layer** (`src/pdf_merge_gui/services/`) may depend on `domain` and `utils`.
   - Services encapsulate use-case logic and sequence/preview behavior.
3. **`adapters` layer** (`src/pdf_merge_gui/adapters/`) isolates third-party library usage.
   - `src/pdf_merge_gui/adapters/pypdf_adapter.py` is where `pypdf` integration belongs.
   - `fitz` (PyMuPDF) integration should also remain in adapter-style boundary code (for example preview integration modules), not leak into UI.
4. **`domain` layer** (`src/pdf_merge_gui/domain/`) remains pure data objects.
   - Domain models should avoid UI, adapter, and third-party concerns.

## Consequences

- UI behavior becomes easier to test because it targets stable model/service contracts.
- Third-party library churn (`pypdf`, `fitz`) is localized.
- Future refactors can move or split adapters with minimal impact to higher layers.
- New modules should be placed according to these boundaries, and cross-layer imports should be treated as architecture violations.

## Notes on existing modules

- `src/pdf_merge_gui/model.py` currently acts as a façade for sequence operations and merge output orchestration.
- As implementation evolves, model/service boundaries may be adjusted, but UI must continue to avoid direct dependency on adapter internals.
