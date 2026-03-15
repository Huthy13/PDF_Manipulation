from pdf_merge_gui.services.telemetry import Telemetry, TimingAggregation


def test_telemetry_noop_when_disabled():
    telemetry = Telemetry(enabled=False)

    telemetry.increment("event")
    with telemetry.time_block("timed"):
        pass

    assert telemetry.get_count("event") == 0
    assert telemetry.get_timing("timed")["count"] == 0


def test_telemetry_collects_counts_and_timing_when_enabled():
    telemetry = Telemetry(enabled=True)

    telemetry.increment("preview_cache_hit")
    telemetry.increment("preview_cache_hit")
    with telemetry.time_block("render"):
        pass

    timing = telemetry.get_timing("render")
    assert telemetry.get_count("preview_cache_hit") == 2
    assert timing["count"] == 1
    assert timing["total_ms"] >= 0


def test_timing_aggregation_uses_bounded_sample_window():
    aggregation = TimingAggregation(max_samples=8)

    for sample in range(100):
        aggregation.add_sample(float(sample))

    assert aggregation.count == 100
    assert len(aggregation.samples_ms) == 8
    assert list(aggregation.samples_ms) == [92.0, 93.0, 94.0, 95.0, 96.0, 97.0, 98.0, 99.0]


def test_timing_aggregation_to_dict_schema_is_stable():
    aggregation = TimingAggregation()

    metrics = aggregation.to_dict()

    assert list(metrics.keys()) == [
        "count",
        "total_ms",
        "avg_ms",
        "min_ms",
        "p50_ms",
        "p95_ms",
        "max_ms",
    ]


def test_timing_aggregation_percentiles_from_deterministic_set():
    aggregation = TimingAggregation(max_samples=20)

    for sample in [5.0, 10.0, 15.0, 20.0, 25.0]:
        aggregation.add_sample(sample)

    metrics = aggregation.to_dict()

    assert metrics["count"] == 5
    assert metrics["p50_ms"] == 15.0
    assert metrics["p95_ms"] == 20.0
