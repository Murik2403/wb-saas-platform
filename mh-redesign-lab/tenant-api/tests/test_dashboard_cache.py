from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Lock
from threading import Event
from time import sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard_cache import DashboardResponseCache
from test_display_contract import api


def test_identical_requests_share_one_expensive_build() -> None:
    cache = DashboardResponseCache()
    calls = 0
    calls_lock = Lock()

    def builder() -> dict[str, bool]:
        nonlocal calls
        with calls_lock:
            calls += 1
        sleep(0.03)
        return {"ready": True}

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: cache.get_or_build("today", builder), range(4)))

    assert calls == 1
    assert results == [{"ready": True}] * 4


def test_different_pages_do_not_build_large_dashboards_in_parallel() -> None:
    cache = DashboardResponseCache()
    active = 0
    peak = 0
    active_lock = Lock()

    def builder() -> str:
        nonlocal active, peak
        with active_lock:
            active += 1
            peak = max(peak, active)
        sleep(0.03)
        with active_lock:
            active -= 1
        return "ready"

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda key: cache.get_or_build(key, builder), ["today", "stock"]))

    assert peak == 1


def test_clear_during_build_does_not_repopulate_stale_cache():
    cache = DashboardResponseCache()
    started, release = Event(), Event()

    def old_builder():
        started.set()
        assert release.wait(2)
        return "old"

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(cache.get_or_build, "today", old_builder)
        assert started.wait(2)
        cache.clear()
        release.set()
        pending.result()
    assert cache.get_or_build("today", lambda: "new") == "new"


def test_finance_and_overview_reuse_same_period_dashboard(monkeypatch) -> None:
    api.dashboard_data_cache.clear()
    calls = 0

    def build(start: date, end: date) -> tuple[date, date]:
        nonlocal calls
        calls += 1
        return start, end

    monkeypatch.setattr(api, "build_dashboard", build)
    start, end = date(2026, 7, 17), date(2026, 9, 24)

    first = api._build_dashboard_cached(start, end)
    second = api._build_dashboard_cached(start, end)

    assert calls == 1
    assert first == second == (start, end)
    api._clear_dashboard_caches()
    assert api._build_dashboard_cached(start, end) == (start, end)
    assert calls == 2
    api._clear_dashboard_caches()


def test_overview_can_skip_previous_window_for_finance_page(monkeypatch) -> None:
    calls: list[tuple[date, date]] = []
    previous_contexts: list[object] = []
    cache_keys: list[str] = []
    monkeypatch.setattr(api, "table_count", lambda _table: 1)
    monkeypatch.setattr(api, "_build_dashboard_cached", lambda start, end: calls.append((start, end)) or object())
    monkeypatch.setattr(api, "compute_overview", lambda _ctx, previous: previous_contexts.append(previous) or {"kpis": []})
    monkeypatch.setattr(api, "_dashboard_response", lambda page, builder: cache_keys.append(page) or builder())

    response = api.get_overview(
        period="custom",
        start=date(2026, 7, 17),
        end=date(2026, 9, 23),
        compare_previous=False,
    )

    assert response == {"kpis": []}
    assert len(calls) == 1
    assert previous_contexts == [None]
    assert cache_keys[0].endswith(":selected-only")
