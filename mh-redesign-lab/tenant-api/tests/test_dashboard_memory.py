from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Lock
from time import sleep
from types import SimpleNamespace
import gc
import weakref

import pandas as pd
from test_display_contract import api
from dashboard_cache import DashboardResponseCache


def test_heavy_cache_releases_previous_snapshot_before_next_build():
    cache = DashboardResponseCache(max_entries=1, release_before_build=True)
    first = cache.get_or_build("a", lambda: SimpleNamespace(frame=pd.DataFrame({"x": range(100_000)})))
    reference = weakref.ref(first.frame)
    del first
    def second():
        gc.collect()
        assert reference() is None
        return "b"
    assert cache.get_or_build("b", second) == "b"
    assert list(cache._values) == ["b"]


def test_context_metadata_is_isolated_without_copying_heavy_frames(monkeypatch):
    api.context_response_cache.clear()
    api.dashboard_data_cache.clear()
    class Snapshot:
        def __init__(self):
            self.products = pd.DataFrame({"id": range(100_000), "value": range(100_000)})
        def __deepcopy__(self, memo):
            raise AssertionError("DashboardData must not be deep copied")
    snapshot = Snapshot()
    today = date(2026, 9, 30)
    monkeypatch.setattr(api, "_build_ctx_uncached", lambda: {"today_msk": today, "settings": {"nested": [1]}})
    monkeypatch.setattr(api, "build_dashboard", lambda start, end: snapshot)
    def reader(_):
        ctx = api._build_ctx()
        ctx["settings"]["nested"].append(2)
        assert ctx["data"] is snapshot
        return ctx
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(reader, range(5)))
    assert api._build_ctx()["settings"]["nested"] == [1]
    assert all("data" not in value for _, value in api.context_response_cache._values.values())
    api.context_response_cache.clear()
    api.dashboard_data_cache.clear()


def test_concurrent_period_builds_are_serialized_and_retention_is_one(monkeypatch):
    api.dashboard_data_cache.clear()
    lock = Lock()
    active = peak = 0
    def build(start, end):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        frame = pd.DataFrame({"id": range(100_000), "value": range(100_000)})
        sleep(0.02)
        with lock:
            active -= 1
        return SimpleNamespace(products=frame)
    monkeypatch.setattr(api, "build_dashboard", build)
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda day: api._build_dashboard_cached(date(2026, 9, day), date(2026, 9, 30)), range(1, 6)))
    assert peak == 1
    assert len(api.dashboard_data_cache._values) == 1
    api.dashboard_data_cache.clear()
