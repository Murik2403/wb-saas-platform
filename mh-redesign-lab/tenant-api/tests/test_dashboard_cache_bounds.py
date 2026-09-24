from __future__ import annotations

from dashboard_cache import DashboardResponseCache


def test_insertion_prunes_all_expired_entries(monkeypatch) -> None:
    now = 10.0
    monkeypatch.setattr("dashboard_cache.monotonic", lambda: now)
    cache = DashboardResponseCache(ttl_seconds=5, max_entries=8)

    cache.get_or_build("period-a", lambda: "a")
    cache.get_or_build("period-b", lambda: "b")
    assert len(cache._values) == 2

    now = 15.0
    cache.get_or_build("period-c", lambda: "c")

    assert list(cache._values) == ["period-c"]
    assert cache.get_or_build("period-c", lambda: "rebuilt") == "c"


def test_capacity_evicts_least_recently_used_entry(monkeypatch) -> None:
    now = 20.0
    monkeypatch.setattr("dashboard_cache.monotonic", lambda: now)
    cache = DashboardResponseCache(ttl_seconds=30, max_entries=2)

    cache.get_or_build("period-a", lambda: "a")
    now += 1
    cache.get_or_build("period-b", lambda: "b")
    now += 1
    assert cache.get_or_build("period-a", lambda: "rebuilt-a") == "a"
    cache.get_or_build("period-c", lambda: "c")

    assert list(cache._values) == ["period-a", "period-c"]
    assert cache.get_or_build("period-b", lambda: "rebuilt-b") == "rebuilt-b"

