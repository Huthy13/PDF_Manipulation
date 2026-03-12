from pdf_merge_gui.services.telemetry import Telemetry


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
