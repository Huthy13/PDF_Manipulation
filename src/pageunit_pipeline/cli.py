"""Command-line entrypoint for running the PageUnit pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from pageunit_pipeline.ocr.tesseract_adapter import TesseractOcrAdapter
from pageunit_pipeline.pipeline.orchestrator import DocumentPipelineOrchestrator
from pageunit_pipeline.pipeline.serialize import serialize_pageunits


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run page-unit extraction over a PDF.")
    parser.add_argument("source", help="Input PDF path")
    parser.add_argument(
        "--output-json",
        default="pageunits.json",
        help="Output JSON array path (default: pageunits.json)",
    )
    parser.add_argument(
        "--output-ndjson",
        default=None,
        help="Optional NDJSON output path",
    )
    parser.add_argument(
        "--enable-ocr",
        action="store_true",
        help="Enable OCR for pages selected by OCR decisioning",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    orchestrator = DocumentPipelineOrchestrator(
        ocr_adapter=TesseractOcrAdapter(enabled=args.enable_ocr)
    )
    result = orchestrator.run(Path(args.source))

    pages = [artifact.page_unit for artifact in result.pages]
    json_path, ndjson_path = serialize_pageunits(
        pages,
        json_output_path=args.output_json,
        ndjson_output_path=args.output_ndjson,
    )

    print(f"Wrote JSON: {json_path}")
    if ndjson_path is not None:
        print(f"Wrote NDJSON: {ndjson_path}")

    if result.summary is not None:
        print(
            "Summary: "
            f"pages={result.summary.total_pages}, "
            f"native={result.summary.native_pages}, "
            f"ocr={result.summary.ocr_pages}, "
            f"hybrid={result.summary.hybrid_pages}, "
            f"failed={result.summary.failed_pages}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
