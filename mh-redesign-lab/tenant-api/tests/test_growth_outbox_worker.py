from __future__ import annotations

import asyncio
from contextlib import nullcontext
import importlib.util
from pathlib import Path
import sys

import pytest

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))

from growth_outbox_worker import delivery_worker_enabled, run_growth_outbox_worker


def test_worker_is_disabled_for_demo_or_incomplete_transport():
    assert not delivery_worker_enabled(
        is_demo=True,
        internal_api_url="https://gateway.example.test",
        internal_api_secret="secret",
        tenant_slug="tenant",
    )
    assert not delivery_worker_enabled(
        is_demo=False,
        internal_api_url="https://gateway.example.test",
        internal_api_secret="",
        tenant_slug="tenant",
    )
    assert not delivery_worker_enabled(
        is_demo=False,
        internal_api_url="https://gateway.example.test",
        internal_api_secret="secret",
        tenant_slug="",
    )
    assert delivery_worker_enabled(
        is_demo=False,
        internal_api_url="https://gateway.example.test",
        internal_api_secret="secret",
        tenant_slug="tenant",
    )
    for unsafe_url in (
        "http://gateway.example.test",
        "https://user:password@gateway.example.test",
        "https://gateway.example.test/path",
        "https://gateway.example.test/?secret=value",
        "https://gateway.example.test/#fragment",
        "https://gateway.example.test:invalid",
        "https:///missing-host",
    ):
        assert not delivery_worker_enabled(
            is_demo=False,
            internal_api_url=unsafe_url,
            internal_api_secret="secret",
            tenant_slug="tenant",
        )


def test_worker_attempts_delivery_then_stops_when_requested(tmp_path):
    async def scenario():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        calls = []

        def deliver(path, *, limit):
            assert limit == 1
            calls.append(path)
            loop.call_soon_threadsafe(stop.set)
            return {"status": "complete", "attempted": 0, "delivered": 0}

        await run_growth_outbox_worker(
            stop,
            tmp_path / "tenant.sqlite3",
            deliver=deliver,
            interval_seconds=60,
        )
        assert calls == [tmp_path / "tenant.sqlite3"]

    asyncio.run(scenario())


def test_worker_retries_after_unexpected_failure_without_logging_exception(tmp_path, caplog):
    async def scenario():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        calls = []

        def deliver(path, *, limit):
            assert limit == 1
            calls.append(path)
            if len(calls) == 1:
                raise RuntimeError("do-not-log-this-secret")
            loop.call_soon_threadsafe(stop.set)
            return {"status": "complete", "attempted": 0, "delivered": 0}

        await run_growth_outbox_worker(
            stop,
            tmp_path / "tenant.sqlite3",
            deliver=deliver,
            interval_seconds=0.01,
        )
        assert len(calls) == 2

    asyncio.run(scenario())
    assert "will retry" in caplog.text
    assert "do-not-log-this-secret" not in caplog.text


def test_worker_reports_only_sanitized_delivery_status(tmp_path, caplog):
    async def scenario():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def deliver(_path, *, limit):
            assert limit == 1
            loop.call_soon_threadsafe(stop.set)
            return {"status": "storage_unavailable", "delivered": 0, "error": "private detail"}

        await run_growth_outbox_worker(stop, tmp_path / "tenant.sqlite3", deliver=deliver)

    asyncio.run(scenario())
    assert "status=storage_unavailable" in caplog.text
    assert "private detail" not in caplog.text


def test_api_lifespan_owns_worker_startup_and_shutdown(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
    spec = importlib.util.spec_from_file_location("growth_worker_lifespan_test_api", API_DIR / "api_server.py")
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    spec.loader.exec_module(api)

    monkeypatch.setattr(api, "DB_PATH", tmp_path / "tenant.sqlite3")
    monkeypatch.setattr(api, "IS_DEMO", False)
    monkeypatch.setattr(api, "INTERNAL_API_URL", "https://gateway.example.test")
    monkeypatch.setattr(api, "INTERNAL_API_SECRET", "test-only")
    monkeypatch.setattr(api, "TENANT_SLUG", "test-tenant")
    monkeypatch.setattr(api, "_startup", lambda: None)
    monkeypatch.setattr(api, "sync_lock", lambda _path: nullcontext())
    monkeypatch.setenv("MH_LOCAL_JOBS", "0")
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def fake_worker(stop_event, _db_path):
        started.set()
        await stop_event.wait()
        stopped.set()

    monkeypatch.setattr(api, "run_growth_outbox_worker", fake_worker)

    async def scenario():
        async with api.lifespan(api.app):
            await asyncio.wait_for(started.wait(), timeout=1)
        assert stopped.is_set()

    asyncio.run(scenario())
    assert "Growth outbox retry worker started" in caplog.text
    assert "Growth outbox retry worker stopped" in caplog.text
