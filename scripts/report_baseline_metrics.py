from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pdf_merge_gui.model import MergeModel  # noqa: E402
from pdf_merge_gui.services.preview_service import PreviewService  # noqa: E402
from pdf_merge_gui.services import telemetry as telemetry_module  # noqa: E402
from pdf_merge_gui.services.telemetry import Telemetry  # noqa: E402

_FIXTURE_SETUP = ROOT / "tests" / "fixtures" / "setup_perf_fixtures.py"
_spec = importlib.util.spec_from_file_location("setup_perf_fixtures", _FIXTURE_SETUP)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"Unable to load fixture setup module: {_FIXTURE_SETUP}")
_fixture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture_module)
ensure_perf_fixtures = _fixture_module.ensure_perf_fixtures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report baseline telemetry and perf metrics for CI.")
    parser.add_argument("--baseline", default="tests/perf_baseline_metrics.json", help="Path to baseline JSON")
    parser.add_argument(
        "--output", default="artifacts/baseline_metrics.json", help="Path for emitted machine-readable metrics JSON"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.20,
        help="Allowed regression percentage as a decimal (0.20 = 20%%)",
    )
    parser.add_argument(
        "--absolute-duration-slack-ms",
        type=float,
        default=150.0,
        help="Absolute duration slack in milliseconds to absorb runner variance",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Store measured values as the new baseline",
    )
    return parser.parse_args()


def collect_metrics(pdf_paths: list[Path]) -> dict[str, dict[str, float | int]]:
    telemetry = Telemetry(enabled=True)
    telemetry_module.DEFAULT_TELEMETRY = telemetry

    model = MergeModel()
    for path in pdf_paths:
        model.add_pdf(str(path))

    from pdf_merge_gui.services import preview_service as preview_module

    preview_module.ImageTk.PhotoImage = lambda image: image  # type: ignore[assignment,misc]
    preview = PreviewService(cache_size=128)
    preview_targets: list[tuple[str, int, float]] = []
    for path in pdf_paths:
        for page_index in range(3):
            preview_targets.append((str(path), page_index, 1.25))

    for source_path, page_index, zoom in preview_targets:
        preview.render(source_path, page_index, zoom)
    for _ in range(20):
        for source_path, page_index, zoom in preview_targets:
            preview.render(source_path, page_index, zoom)

    selected = model.sequence[:20]
    model.sequence.clear()
    model.sequence.extend(selected)
    with tempfile.TemporaryDirectory() as temp_dir:
        out = Path(temp_dir) / "telemetry-merged.pdf"
        model.write_merged(str(out))

    load_timing = telemetry.get_timing("load_pdf_pages")
    export_timing = telemetry.get_timing("write_merged")
    cache_hit_count = telemetry.get_count("preview_cache_hit")
    cache_miss_count = telemetry.get_count("preview_cache_miss")
    cache_total = cache_hit_count + cache_miss_count
    cache_hit_ratio = cache_hit_count / cache_total if cache_total else 0.0

    metrics = {
        "load_pdf_pages": {
            "total_calls": telemetry.get_count("load_pdf_pages_calls"),
            "total_pages": telemetry.get_count("load_pdf_pages_pages_loaded"),
            "avg_duration_ms": round(float(load_timing["avg_ms"]), 3),
            "p95_duration_ms": round(float(load_timing["p95_ms"]), 3),
        },
        "preview_cache": {
            "hit_count": cache_hit_count,
            "miss_count": cache_miss_count,
            "hit_ratio": round(cache_hit_ratio, 6),
        },
        "export_merged_file": {
            "call_count": int(export_timing["count"]),
            "pages_exported": telemetry.get_count("write_merged_pages_exported"),
            "avg_duration_ms": round(float(export_timing["avg_ms"]), 3),
            "p95_duration_ms": round(float(export_timing["p95_ms"]), 3),
        },
    }

    model.clear()
    return metrics


def compare_to_baseline(
    current: dict[str, dict[str, float | int]],
    baseline: dict[str, dict[str, float | int]],
    threshold: float,
    absolute_duration_slack_ms: float,
) -> list[str]:
    failures: list[str] = []

    duration_keys = {"avg_duration_ms", "p95_duration_ms"}
    higher_is_better_keys = {"hit_ratio"}

    for category, values in current.items():
        baseline_values = baseline.get(category)
        if not baseline_values:
            continue
        for key, current_value in values.items():
            if key not in baseline_values:
                continue

            current_num = float(current_value)
            baseline_num = float(baseline_values[key])

            if key in duration_keys:
                allowed = max(baseline_num * (1.0 + threshold), baseline_num + absolute_duration_slack_ms)
                if current_num > allowed:
                    failures.append(
                        f"{category}.{key} regressed: current={current_num:.3f} baseline={baseline_num:.3f} max={allowed:.3f}"
                    )
            elif key in higher_is_better_keys:
                minimum = baseline_num * (1.0 - threshold)
                if current_num < minimum:
                    failures.append(
                        f"{category}.{key} regressed: current={current_num:.6f} baseline={baseline_num:.6f} min={minimum:.6f}"
                    )

    return failures


def print_markdown_summary(
    metrics: dict[str, dict[str, float | int]],
    baseline_path: Path,
    threshold: float,
    failures: list[str],
) -> None:
    load = metrics["load_pdf_pages"]
    cache = metrics["preview_cache"]
    export = metrics["export_merged_file"]

    print("## Baseline Metrics Summary")
    print("")
    print("### Load PDF pages")
    print(
        f"- total calls: **{load['total_calls']}**, total pages: **{load['total_pages']}**, "
        f"avg/p95 duration: **{load['avg_duration_ms']}ms / {load['p95_duration_ms']}ms**"
    )
    print("")
    print("### Preview cache")
    print(
        f"- hit count: **{cache['hit_count']}**, miss count: **{cache['miss_count']}**, "
        f"hit ratio: **{float(cache['hit_ratio']) * 100:.2f}%**"
    )
    print("")
    print("### Export merged file")
    print(
        f"- call count: **{export['call_count']}**, pages exported: **{export['pages_exported']}**, "
        f"avg/p95 duration: **{export['avg_duration_ms']}ms / {export['p95_duration_ms']}ms**"
    )
    print("")
    if not baseline_path.exists():
        print(f"⚠️ Baseline file `{baseline_path}` does not exist yet; run with `--update-baseline`.")
        return

    status = "✅ PASS" if not failures else "❌ FAIL"
    print(f"### Baseline comparison ({status})")
    print(f"- threshold: **{threshold:.0%}**")
    print(f"- baseline: `{baseline_path}`")
    if failures:
        print("- regressions:")
        for failure in failures:
            print(f"  - {failure}")
    else:
        print("- no regressions detected")


def main() -> int:
    args = parse_args()

    baseline_path = ROOT / args.baseline
    output_path = ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pdf_paths = ensure_perf_fixtures()
    metrics = collect_metrics(pdf_paths)
    output_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.update_baseline or not baseline_path.exists():
        baseline_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print_markdown_summary(metrics, baseline_path, args.threshold, failures=[])
        print(f"\nBaseline written to {baseline_path}")
        return 0

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    failures = compare_to_baseline(metrics, baseline, args.threshold, args.absolute_duration_slack_ms)
    print_markdown_summary(metrics, baseline_path, args.threshold, failures)

    if failures:
        print("\nTip: rerun with --update-baseline when intentional recalibration is needed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
