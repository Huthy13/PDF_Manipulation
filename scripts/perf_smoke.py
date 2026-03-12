from __future__ import annotations

import argparse
import importlib.util  # noqa: E402
import json
import statistics
import sys
import tempfile
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pdf_merge_gui.model import MergeModel  # noqa: E402
from pdf_merge_gui.services.preview_service import PreviewService  # noqa: E402

_FIXTURE_SETUP = ROOT / "tests" / "fixtures" / "setup_perf_fixtures.py"
_spec = importlib.util.spec_from_file_location("setup_perf_fixtures", _FIXTURE_SETUP)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"Unable to load fixture setup module: {_FIXTURE_SETUP}")
_fixture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture_module)
ensure_perf_fixtures = _fixture_module.ensure_perf_fixtures


def _ms(start: float, end: float) -> float:
    return (end - start) * 1000


def _median(samples: list[float]) -> float:
    return float(statistics.median(samples))


def measure_page_load(pdf_paths: list[Path], repeats: int = 3) -> float:
    samples: list[float] = []
    for _ in range(repeats):
        model = MergeModel()
        start = perf_counter()
        for path in pdf_paths:
            model.add_pdf(str(path))
        samples.append(_ms(start, perf_counter()))
        model.clear()
    return _median(samples)


def measure_preview_cycle(pdf_paths: list[Path], repeats: int = 3) -> tuple[float, float]:
    from pdf_merge_gui.services import preview_service as preview_module

    targets: list[tuple[str, int, float]] = []
    for path in pdf_paths:
        for page_index in range(3):
            targets.append((str(path), page_index, 1.25))

    miss_samples: list[float] = []
    hit_samples: list[float] = []

    for _ in range(repeats):
        preview_module.ImageTk.PhotoImage = lambda image: image  # type: ignore[assignment,misc]
        preview = PreviewService(cache_size=128)

        miss_start = perf_counter()
        for source_path, page_index, zoom in targets:
            preview.render(source_path, page_index, zoom)
        miss_samples.append(_ms(miss_start, perf_counter()))

        hit_start = perf_counter()
        for _ in range(50):
            for source_path, page_index, zoom in targets:
                preview.render(source_path, page_index, zoom)
        hit_samples.append(_ms(hit_start, perf_counter()))

    return _median(miss_samples), _median(hit_samples)


def measure_merged_export(pdf_paths: list[Path], repeats: int = 3) -> float:
    samples: list[float] = []
    for _ in range(repeats):
        model = MergeModel()
        for path in pdf_paths:
            model.add_pdf(str(path))

        selected = model.sequence[:20]
        model.sequence.clear()
        model.sequence.extend(selected)

        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "perf-merged.pdf"
            start = perf_counter()
            model.write_merged(str(out))
            samples.append(_ms(start, perf_counter()))

        model.clear()
    return _median(samples)


def compare_to_baseline(current: dict[str, float], baseline: dict[str, float], threshold: float) -> list[str]:
    failures: list[str] = []
    absolute_slack_ms = 150.0
    for key, current_value in current.items():
        baseline_value = baseline.get(key)
        if baseline_value is None:
            continue
        allowed = max(baseline_value * (1.0 + threshold), baseline_value + absolute_slack_ms)
        if current_value > allowed:
            failures.append(
                f"{key}: current={current_value:.2f}ms baseline={baseline_value:.2f}ms limit={allowed:.2f}ms"
            )
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Perf smoke check for PDF merge backend")
    parser.add_argument("--baseline", default="tests/perf_baseline.json", help="Path to baseline JSON")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.20,
        help="Allowed regression percentage as a decimal (0.20 = 20%%)",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Store measured values as the new baseline",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    baseline_path = ROOT / args.baseline

    pdf_paths = ensure_perf_fixtures()

    page_load_ms = measure_page_load(pdf_paths)
    preview_miss_ms, preview_hit_ms = measure_preview_cycle(pdf_paths)
    merged_export_ms = measure_merged_export(pdf_paths)

    metrics = {
        "page_load_ms": round(page_load_ms, 3),
        "preview_render_miss_ms": round(preview_miss_ms, 3),
        "preview_render_hit_ms": round(preview_hit_ms, 3),
        "merged_export_ms": round(merged_export_ms, 3),
    }

    print(json.dumps(metrics, indent=2, sort_keys=True))

    if args.update_baseline or not baseline_path.exists():
        baseline_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Baseline written to {baseline_path}")
        return 0

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    failures = compare_to_baseline(metrics, baseline, args.threshold)
    if failures:
        print("Performance regression detected:")
        for failure in failures:
            print(f" - {failure}")
        print("Tip: rerun with --update-baseline when intentional improvements/recalibrations are needed.")
        return 1

    print(f"Perf smoke passed against baseline (threshold={args.threshold:.0%}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
