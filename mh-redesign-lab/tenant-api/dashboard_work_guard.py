"""Bound dashboard heap use across complete requests, not only cache misses."""
import asyncio
from contextlib import suppress
from contextvars import ContextVar


_request_lease = ContextVar("dashboard_request_lease", default=None)


def guarded_request(scope):
    path, method = scope.get("path", ""), scope.get("method", "GET")
    # Optional upstream analytics uses its own small stock-history frame. Never
    # make local financial reports queue behind WB's rate-limit backoff.
    if path in {"/api/v1/dashboard/analytics", "/api/v1/dashboard/finance/wb-costs"}:
        return False
    if method in {"GET", "HEAD"}:
        return (path.startswith(("/api/v1/dashboard/", "/api/v1/operations/finance/", "/api/v1/ozon-insights/", "/api/v1/ozon-cabinet/"))
                or path in {"/api/v1/ozon-accounting/normalization/documents", "/api/v1/ozon-accounting/normalization/preview", "/api/v1/ozon-accounting/pnl", "/api/v1/ozon-accounting/workspace", "/api/today", "/api/overview", "/api/products", "/api/v1/settings/expense-coverage"})
    return method == "POST" and path in {"/api/v1/ozon-insights/tax-input", "/api/v1/dashboard/products/price-advice", "/api/v1/settings/expense-coverage"}


class DashboardWorkGuard:
    def __init__(self, app):
        self.app = app
        self._loop = None
        self._gate = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not guarded_request(scope):
            return await self.app(scope, receive, send)
        lease = _request_lease.get()
        if lease is not None and lease["active"]:
            # In-process child-shop ASGI dispatch belongs to the parent's held
            # budget. This ownership comes from Python context, never headers.
            return await self.app(scope, receive, send)
        loop = asyncio.get_running_loop()
        # Uvicorn has one loop per worker. Also permit independent TestClient
        # lifecycles without reusing a semaphore bound to a closed test loop.
        if self._loop is not loop:
            self._loop, self._gate = loop, asyncio.Semaphore(1)
        gate = self._gate
        acquired = asyncio.create_task(gate.acquire())
        buffered = []
        monitor = None
        lease = None
        lease_token = None

        async def disconnected():
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                buffered.append(message)
                # Unusual GET bodies are replayed intact; don't eagerly drain
                # a large chunked body into memory while waiting for the slot.
                if message.get("more_body"):
                    await asyncio.Future()

        async def replay_receive():
            if buffered:
                return buffered.pop(0)
            return await receive()

        try:
            # Do not race a reader against POST's body consumer.
            if scope.get("method") in {"GET", "HEAD"}:
                monitor = asyncio.create_task(disconnected())
                done, _ = await asyncio.wait({acquired, monitor}, return_when=asyncio.FIRST_COMPLETED)
                if monitor in done:
                    return
            else:
                await acquired
            if monitor:
                monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor
            lease = {"active": True}
            lease_token = _request_lease.set(lease)
            running = asyncio.create_task(self.app(scope, replay_receive, send))
            try:
                await asyncio.shield(running)
            except asyncio.CancelledError:
                # A sync handler may still occupy a worker thread after its
                # request is cancelled. Keep the heap slot until it really
                # finishes, rather than admitting another large calculation.
                while not running.done():
                    with suppress(asyncio.CancelledError):
                        await asyncio.shield(running)
                with suppress(Exception, asyncio.CancelledError):
                    running.result()
                raise
        finally:
            if lease is not None:
                lease["active"] = False
                _request_lease.reset(lease_token)
            if monitor and not monitor.done():
                monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor
            if not acquired.done():
                acquired.cancel()
                with suppress(asyncio.CancelledError):
                    await acquired
            elif not acquired.cancelled() and acquired.exception() is None and acquired.result():
                gate.release()
