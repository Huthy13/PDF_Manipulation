from pdf_merge_gui.utils.cache import LRUCache


def test_lru_cache_eviction():
    cache: LRUCache[str, int] = LRUCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)

    assert cache.get("a") == 1

    cache.put("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_lru_cache_evicts_by_memory_cost_before_count_limit() -> None:
    cache: LRUCache[str, int] = LRUCache(capacity=10, max_cost=5, cost_fn=lambda v: v)

    cache.put("a", 3)
    cache.put("b", 3)

    assert cache.get("a") is None
    assert cache.get("b") == 3
    assert cache.total_cost == 3
