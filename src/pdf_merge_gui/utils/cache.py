from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Generic, Literal, TypeVar

K = TypeVar("K")
V = TypeVar("V")
EvictionReason = Literal["capacity", "memory", "replace", "clear", "prefix_clear"]


@dataclass(frozen=True)
class CacheEntry(Generic[V]):
    value: V
    cost: int


class LRUCache(Generic[K, V]):
    def __init__(
        self,
        capacity: int = 100,
        *,
        max_cost: int | None = None,
        cost_fn: Callable[[V], int] | None = None,
        on_evict: Callable[[K, V, int, EvictionReason], None] | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if max_cost is not None and max_cost < 1:
            raise ValueError("max_cost must be at least 1 when provided")
        self.capacity = capacity
        self.max_cost = max_cost
        self._cost_fn = cost_fn or (lambda _value: 1)
        self._on_evict = on_evict
        self._total_cost = 0
        self._cache: OrderedDict[K, CacheEntry[V]] = OrderedDict()

    @property
    def total_cost(self) -> int:
        return self._total_cost

    def _evict(self, key: K, reason: EvictionReason) -> None:
        entry = self._cache.pop(key, None)
        if entry is None:
            return
        self._total_cost = max(0, self._total_cost - entry.cost)
        if self._on_evict is not None:
            self._on_evict(key, entry.value, entry.cost, reason)

    def _evict_if_needed(self) -> None:
        while len(self._cache) > self.capacity:
            eldest_key = next(iter(self._cache))
            self._evict(eldest_key, "capacity")

        if self.max_cost is None:
            return

        while self._cache and self._total_cost > self.max_cost:
            eldest_key = next(iter(self._cache))
            self._evict(eldest_key, "memory")

    def get(self, key: K) -> V | None:
        if key not in self._cache:
            return None
        entry = self._cache.pop(key)
        self._cache[key] = entry
        return entry.value

    def put(self, key: K, value: V) -> None:
        if key in self._cache:
            self._evict(key, "replace")

        cost = max(1, int(self._cost_fn(value)))
        self._cache[key] = CacheEntry(value=value, cost=cost)
        self._total_cost += cost
        self._evict_if_needed()

    def clear(self) -> None:
        for key in list(self._cache.keys()):
            self._evict(key, "clear")

    def remove_matching_prefix(self, prefix: str) -> None:
        doomed = [key for key in self._cache if isinstance(key, tuple) and key and key[0] == prefix]
        for key in doomed:
            self._evict(key, "prefix_clear")
