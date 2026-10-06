import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_work_guard import DashboardWorkGuard, guarded_request


def scope(path, method="GET", **extra):
    return {"type": "http", "path": path, "method": method, **extra}


async def never_receive():
    return await asyncio.Future()


async def discard(message):
    pass


@pytest.mark.parametrize("path,method", [
    ("/api/v1/dashboard/management-pnl/monthly", "GET"),
    ("/api/v1/dashboard/finance/fifo-reconciliation", "GET"),
    ("/api/v1/dashboard/management-economics", "GET"),
    ("/api/v1/operations/finance/export", "HEAD"),
    ("/api/v1/dashboard/products/price-advice", "POST"),
    ("/api/overview", "GET"), ("/api/today", "GET"), ("/api/products", "GET"),
    ("/api/v1/settings/expense-coverage", "POST"),
    ("/api/v1/ozon-insights/products", "GET"),
    ("/api/v1/ozon-insights/monthly", "GET"),
    ("/api/v1/ozon-accounting/normalization/preview", "GET"),
    ("/api/v1/ozon-insights/tax-input", "POST"),
])
def test_heavy_route_coverage(path, method):
    assert guarded_request(scope(path, method))


def test_ozon_upstream_reports_do_not_block_local_finance():
    for path, method in [("/api/v1/ozon-accounting/sync", "POST"), ("/api/v1/ozon-accounting/advertising/reports/id/poll", "POST")]:
        assert not guarded_request(scope(path, method))


def test_gateway_wiring_surrounds_selected_shop_dispatch():
    from test_display_contract import api
    from gateway_request_auth import GatewayIdentityGuard
    assert api.app.user_middleware[0].cls is GatewayIdentityGuard
    assert api.app.user_middleware[1].cls is DashboardWorkGuard


def test_complete_body_lifetime_serializes_distinct_routes_and_selected_shop():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        paths = []
        async def handler(s, r, send):
            paths.append(s["path"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"serialized"})
        async def slow_send(message):
            if message["type"] == "http.response.body":
                entered.set()
                await release.wait()
        guard = DashboardWorkGuard(handler)
        first = asyncio.create_task(guard(scope("/api/v1/dashboard/finance"), never_receive, slow_send))
        await entered.wait()
        # Header-selected child-shop dispatch is inside the same guard.
        second = asyncio.create_task(guard(scope("/api/v1/dashboard/management-pnl/monthly", headers=[(b"x-mh-shop-id", b"2")]), never_receive, discard))
        third = asyncio.create_task(guard(scope("/api/v1/dashboard/products/price-advice", "POST"), never_receive, discard))
        await asyncio.sleep(0.01)
        assert len(paths) == 1
        # Health and slow external analytics are explicitly outside this gate.
        for path in ["/healthz", "/api/v1/session", "/api/v1/dashboard/analytics", "/api/v1/dashboard/finance/wb-costs"]:
            await asyncio.wait_for(guard(scope(path), never_receive, discard), 0.2)
        release.set()
        await asyncio.gather(first, second, third)
        assert paths[-2:] == ["/api/v1/dashboard/management-pnl/monthly", "/api/v1/dashboard/products/price-advice"]
    asyncio.run(run())


def test_queued_get_disconnect_skips_work_and_queued_post_body_is_not_consumed():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        paths, consumed = [], []
        async def handler(s, r, send):
            paths.append(s["path"])
            if s["path"].endswith("/finance"):
                started.set()
                await release.wait()
            if s["method"] == "POST":
                consumed.extend([await r(), await r()])
        guard = DashboardWorkGuard(handler)
        first = asyncio.create_task(guard(scope("/api/v1/dashboard/finance"), never_receive, discard))
        await started.wait()
        messages = asyncio.Queue()
        await messages.put({"type": "http.request", "body": b"", "more_body": False})
        await messages.put({"type": "http.disconnect"})
        await guard(scope("/api/v1/dashboard/management-pnl/monthly"), messages.get, discard)
        body = [{"type": "http.request", "body": b"first", "more_body": True}, {"type": "http.request", "body": b"second", "more_body": False}]
        calls = []
        async def body_receive():
            calls.append(1)
            return body.pop(0)
        post = asyncio.create_task(guard(scope("/api/v1/dashboard/products/price-advice", "POST"), body_receive, discard))
        await asyncio.sleep(0.01)
        assert not calls
        release.set()
        await asyncio.gather(first, post)
        assert [m["body"] for m in consumed] == [b"first", b"second"]
        assert "/api/v1/dashboard/management-pnl/monthly" not in paths
    asyncio.run(run())


def test_exception_and_queued_cancellation_release_capacity():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def handler(s, r, send):
            calls.append(s["path"])
            if s["path"].endswith("/finance"):
                started.set()
                await release.wait()
                raise ValueError("fixture")
        guard = DashboardWorkGuard(handler)
        first = asyncio.create_task(guard(scope("/api/v1/dashboard/finance"), never_receive, discard))
        await started.wait()
        queued = asyncio.create_task(guard(scope("/api/products"), never_receive, discard))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        release.set()
        with pytest.raises(ValueError):
            await first
        await asyncio.wait_for(guard(scope("/api/today"), never_receive, discard), 0.2)
        assert calls == ["/api/v1/dashboard/finance", "/api/today"]
    asyncio.run(run())


def test_cancelled_active_request_keeps_slot_until_calculation_finishes():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def handler(s, r, send):
            calls.append(s["path"])
            if s["path"].endswith("/finance"):
                started.set()
                await release.wait()
        guard = DashboardWorkGuard(handler)
        first = asyncio.create_task(guard(scope("/api/v1/dashboard/finance"), never_receive, discard))
        await started.wait()
        first.cancel()
        second = asyncio.create_task(guard(scope("/api/products"), never_receive, discard))
        await asyncio.sleep(0.01)
        assert calls == ["/api/v1/dashboard/finance"]
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        assert len(calls) == 2
    asyncio.run(run())


def test_in_process_child_asgi_dispatch_reuses_parent_lease_without_deadlock():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def handler(s, r, send):
            calls.append(s["path"])
            if s.get("child"):
                started.set()
                await release.wait()
            elif s["path"].endswith("/finance"):
                # A child worker in the real service is a separate subprocess;
                # also support embedding its ASGI app in-process safely.
                await guard(scope("/api/v1/dashboard/finance/fifo-reconciliation", child=True), r, send)
        guard = DashboardWorkGuard(handler)
        parent = asyncio.create_task(guard(scope("/api/v1/dashboard/finance", headers=[(b"x-mh-shop-id", b"2")]), never_receive, discard))
        await asyncio.wait_for(started.wait(), 0.2)
        unrelated = asyncio.create_task(guard(scope("/api/products", headers=[(b"x-dashboard-lease", b"true")]), never_receive, discard))
        await asyncio.sleep(0.01)
        assert len(calls) == 2
        release.set()
        await asyncio.gather(parent, unrelated)
        assert calls[-1] == "/api/products"
    asyncio.run(run())


def test_queued_get_request_messages_are_replayed_intact():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        received = []
        async def handler(s, r, send):
            if s["path"].endswith("/finance"):
                started.set()
                await release.wait()
            else:
                received.append(await r())
        guard = DashboardWorkGuard(handler)
        first = asyncio.create_task(guard(scope("/api/v1/dashboard/finance"), never_receive, discard))
        await started.wait()
        messages = asyncio.Queue()
        original = {"type": "http.request", "body": b"preserved", "more_body": False}
        await messages.put(original)
        queued = asyncio.create_task(guard(scope("/api/products"), messages.get, discard))
        await asyncio.sleep(0.01)
        release.set()
        await asyncio.gather(first, queued)
        assert received == [original]
    asyncio.run(run())
