from __future__ import annotations

import os
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Iterator, Mapping


Tags = tuple[tuple[str, str], ...]


def _normalize_tags(tags: Mapping[str, object] | None) -> Tags:
    if not tags:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in tags.items()))


@dataclass
class TimingAggregation:
    """Aggregates latency timings with bounded memory.

    We keep exact running totals/min/max across all observed samples, but retain only a
    rolling window of recent samples for percentile estimation. This bounds memory usage
    while preserving the existing output schema. As a trade-off, ``p50_ms`` and
    ``p95_ms`` are approximate for long-running processes because they are computed from
    the most recent window rather than full history.
    """

    count: int = 0
    total_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    max_samples: int = 512
    samples_ms: deque[float] = field(init=False)

    def __post_init__(self) -> None:
        self.samples_ms = deque(maxlen=self.max_samples)

    def add_sample(self, duration_ms: float) -> None:
        self.count += 1
        self.total_ms += duration_ms
        if self.count == 1:
            self.min_ms = duration_ms
            self.max_ms = duration_ms
        else:
            self.min_ms = min(self.min_ms, duration_ms)
            self.max_ms = max(self.max_ms, duration_ms)
        self.samples_ms.append(duration_ms)

    def to_dict(self) -> dict[str, float | int]:
        if self.count == 0:
            return {
                "count": 0,
                "total_ms": 0.0,
                "avg_ms": 0.0,
                "min_ms": 0.0,
                "p50_ms": 0.0,
                "p95_ms": 0.0,
                "max_ms": 0.0,
            }

        ordered_samples = sorted(self.samples_ms)
        return {
            "count": self.count,
            "total_ms": self.total_ms,
            "avg_ms": self.total_ms / self.count,
            "min_ms": self.min_ms,
            "p50_ms": _percentile(ordered_samples, 0.5),
            "p95_ms": _percentile(ordered_samples, 0.95),
            "max_ms": self.max_ms,
        }


def _percentile(ordered_samples: list[float] | deque[float], quantile: float) -> float:
    if not ordered_samples:
        return 0.0
    index = int((len(ordered_samples) - 1) * quantile)
    return ordered_samples[index]


class Telemetry:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self._counts: dict[tuple[str, Tags], int] = {}
        self._durations: dict[tuple[str, Tags], TimingAggregation] = {}

    def increment(self, name: str, tags: Mapping[str, object] | None = None) -> None:
        if not self.enabled:
            return
        key = (name, _normalize_tags(tags))
        self._counts[key] = self._counts.get(key, 0) + 1

    @contextmanager
    def time_block(self, name: str, tags: Mapping[str, object] | None = None) -> Iterator[None]:
        if not self.enabled:
            yield
            return

        started = perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (perf_counter() - started) * 1000
            key = (name, _normalize_tags(tags))
            aggregation = self._durations.get(key)
            if aggregation is None:
                aggregation = TimingAggregation()
                self._durations[key] = aggregation
            aggregation.add_sample(elapsed_ms)

    def get_count(self, name: str, tags: Mapping[str, object] | None = None) -> int:
        return self._counts.get((name, _normalize_tags(tags)), 0)

    def get_timing(self, name: str, tags: Mapping[str, object] | None = None) -> dict[str, float | int]:
        aggregation = self._durations.get((name, _normalize_tags(tags)))
        if aggregation is None:
            return TimingAggregation().to_dict()
        return aggregation.to_dict()

    def reset(self) -> None:
        self._counts.clear()
        self._durations.clear()


DEFAULT_TELEMETRY = Telemetry(enabled=os.getenv("PDF_MERGE_GUI_TELEMETRY_ENABLED") == "1")


def get_telemetry() -> Telemetry:
    return DEFAULT_TELEMETRY
