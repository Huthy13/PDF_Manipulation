"""Stable JSON/NDJSON serialization helpers for PageUnit outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from pageunit_pipeline.models.page import PageUnit


def write_pageunits_json(
    pages: Iterable[PageUnit],
    output_path: str | Path,
    *,
    indent: int = 2,
) -> Path:
    """Write all page units as one deterministic JSON array file."""

    ordered_pages = _stable_page_payloads(pages)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ordered_pages, indent=indent, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_pageunits_ndjson(
    pages: Iterable[PageUnit],
    output_path: str | Path,
) -> Path:
    """Write one JSON object per line in page-number order."""

    ordered_pages = _stable_page_payloads(pages)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as handle:
        for payload in ordered_pages:
            handle.write(
                json.dumps(payload, ensure_ascii=False, sort_keys=True)
            )
            handle.write("\n")

    return path


def serialize_pageunits(
    pages: Iterable[PageUnit],
    *,
    json_output_path: str | Path,
    ndjson_output_path: str | Path | None = None,
) -> tuple[Path, Path | None]:
    """Serialize page units to JSON and optionally NDJSON."""

    page_list = list(pages)
    json_path = write_pageunits_json(page_list, json_output_path)
    ndjson_path = None

    if ndjson_output_path is not None:
        ndjson_path = write_pageunits_ndjson(page_list, ndjson_output_path)

    return json_path, ndjson_path


def _stable_page_payloads(pages: Iterable[PageUnit]) -> list[dict[str, Any]]:
    page_list = sorted(pages, key=lambda page: (page.page_number, page.extraction_method.value))
    payloads: list[dict[str, Any]] = []

    for page in page_list:
        payloads.append(_stable_map(page.model_dump(mode="json")))

    return payloads


def _stable_map(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _stable_map(value[key]) for key in sorted(value)}

    if isinstance(value, list):
        return [_stable_map(item) for item in value]

    return value
