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
from pdf_merge_gui.services.telemetry import DEFAULT_TELEMETRY  # noqa: E402

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




def _flatten_metrics(metrics: dict[str, float | dict[str, object]], prefix: str = "") -> dict[str, float]:
    flat: dict[str, float] = {}
    for key, value in metrics.items():
        joined = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten_metrics(value, joined))
            continue
        flat[joined] = float(value)
    return flat


def measure_final_preview_navigation(pdf_paths: list[Path], repeats: int = 3) -> dict[str, float]:
    from pdf_merge_gui.services import preview_service as preview_module

    page_refs: list[tuple[str, int]] = []
    for path in pdf_paths:
        if "large" not in path.name:
            continue
        for page_index in range(24):
            page_refs.append((str(path), page_index))
    if not page_refs:
        for path in pdf_paths:
            for page_index in range(12):
                page_refs.append((str(path), page_index))

    viewport_pages = 4
    down_positions = list(range(0, max(len(page_refs) - viewport_pages + 1, 1), 2))
    if down_positions[-1] != max(len(page_refs) - viewport_pages, 0):
        down_positions.append(max(len(page_refs) - viewport_pages, 0))
    oscillation = [
        max(len(page_refs) - viewport_pages - 2, 0),
        max(len(page_refs) - viewport_pages - 6, 0),
        max(len(page_refs) - viewport_pages - 2, 0),
        max(len(page_refs) - viewport_pages - 7, 0),
    ]
    navigation = down_positions + oscillation * 8

    max_frame_samples: list[float] = []
    mean_frame_samples: list[float] = []
    ttfvp_samples: list[float] = []
    hit_ratio_samples: list[float] = []
    bottom_reached: list[float] = []

    for _ in range(repeats):
        preview_module.ImageTk.PhotoImage = lambda image: image  # type: ignore[assignment,misc]
        preview = PreviewService(cache_size=200, offscreen_cache_size=120, ui_cache_size=1, ui_offscreen_cache_size=0)
        DEFAULT_TELEMETRY.enabled = True
        DEFAULT_TELEMETRY.reset()

        reached_last_index = 0
        request_miss = 0
        request_hit = 0

        for pos in navigation:
            visible = list(range(pos, min(pos + viewport_pages, len(page_refs))))
            if visible:
                reached_last_index = max(reached_last_index, visible[-1])

            started = perf_counter()
            first_visible_rendered = False
            first_visible_elapsed_ms = 0.0
            for idx in visible:
                source_path, page_index = page_refs[idx]
                hit_before = DEFAULT_TELEMETRY.get_count("preview_cache_hit")
                miss_before = DEFAULT_TELEMETRY.get_count("preview_cache_miss")
                preview.get_decoded_image(source_path, page_index, 1.25)
                if DEFAULT_TELEMETRY.get_count("preview_cache_hit") > hit_before:
                    request_hit += 1
                elif DEFAULT_TELEMETRY.get_count("preview_cache_miss") > miss_before:
                    request_miss += 1
                if not first_visible_rendered:
                    first_visible_elapsed_ms = _ms(started, perf_counter())
                    first_visible_rendered = True

            frame_ms = _ms(started, perf_counter())
            max_frame_samples.append(frame_ms)
            mean_frame_samples.append(frame_ms)
            if first_visible_rendered:
                ttfvp_samples.append(first_visible_elapsed_ms)

        total_requests = request_hit + request_miss
        hit_ratio = (request_hit / total_requests) if total_requests else 0.0
        hit_ratio_samples.append(hit_ratio)
        bottom_reached.append(1.0 if reached_last_index >= len(page_refs) - 1 else 0.0)

    metrics = {
        "bottom_page_reached": min(bottom_reached),
        "max_frame_update_ms": max(max_frame_samples),
        "avg_frame_update_ms": _median(mean_frame_samples),
        "time_to_first_visible_page_ms": _median(ttfvp_samples),
        "cache_hit_ratio_oscillation": _median(hit_ratio_samples),
    }

    if metrics["bottom_page_reached"] < 1.0:
        raise RuntimeError("Final preview perf scenario could not reach bottom page")
    if metrics["max_frame_update_ms"] > 750.0:
        raise RuntimeError(f"Final preview max frame/update latency too high: {metrics['max_frame_update_ms']:.2f}ms")
    if metrics["avg_frame_update_ms"] > 300.0:
        raise RuntimeError(f"Final preview average frame/update latency too high: {metrics['avg_frame_update_ms']:.2f}ms")
    if metrics["cache_hit_ratio_oscillation"] < 0.50:
        raise RuntimeError(
            f"Final preview cache hit ratio too low under oscillation: {metrics['cache_hit_ratio_oscillation']:.3f}"
        )

    return metrics

def compare_to_baseline(current: dict[str, float | dict[str, object]], baseline: dict[str, float | dict[str, object]], threshold: float) -> list[str]:
    failures: list[str] = []
    absolute_slack_ms = 150.0
    current_flat = _flatten_metrics(current)
    baseline_flat = _flatten_metrics(baseline)
    for key, current_value in current_flat.items():
        baseline_value = baseline_flat.get(key)
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

    final_preview_metrics = measure_final_preview_navigation(pdf_paths)

    metrics = {
        "page_load_ms": round(page_load_ms, 3),
        "preview_render_miss_ms": round(preview_miss_ms, 3),
        "preview_render_hit_ms": round(preview_hit_ms, 3),
        "merged_export_ms": round(merged_export_ms, 3),
        "final_preview": {
            "bottom_page_reached": round(final_preview_metrics["bottom_page_reached"], 3),
            "max_frame_update_ms": round(final_preview_metrics["max_frame_update_ms"], 3),
            "avg_frame_update_ms": round(final_preview_metrics["avg_frame_update_ms"], 3),
            "time_to_first_visible_page_ms": round(final_preview_metrics["time_to_first_visible_page_ms"], 3),
            "cache_hit_ratio_oscillation": round(final_preview_metrics["cache_hit_ratio_oscillation"], 3),
        },
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
