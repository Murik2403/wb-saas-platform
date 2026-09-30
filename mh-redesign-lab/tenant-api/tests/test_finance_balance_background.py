import hashlib
import threading
import time
import types
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from test_finance_api import api


@pytest.fixture
def balance_state(monkeypatch):
    executor = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(api, "_finance_balance_executor", executor, raising=False)
    monkeypatch.setattr(api, "_finance_balance_cache", None, raising=False)
    monkeypatch.setattr(api, "_finance_balance_last_attempt", None, raising=False)
    monkeypatch.setattr(api, "_finance_balance_refreshing", set(), raising=False)
    monkeypatch.setattr(api, "get_token", lambda: "test-token")
    yield
    executor.shutdown(wait=True)


def test_cold_custom_finance_does_not_wait_for_balance(monkeypatch, balance_state):
    entered = threading.Event()
    finished = threading.Event()
    calls = []

    def slow_balance(self):
        calls.append(threading.get_ident())
        entered.set()
        finished.wait(0.7)
        return {"balance": 42}

    monkeypatch.setattr(api.WBAPI, "get_balance", slow_balance)
    monkeypatch.setattr(api, "_build_dashboard_cached", lambda *_: types.SimpleNamespace(
        financial={}, financial_products=pd.DataFrame()))
    monkeypatch.setattr(api, "read_sales_fifo_cogs", lambda *_: pd.DataFrame())
    monkeypatch.setattr(api, "read_financial_fifo_reconciliation", lambda *_: pd.DataFrame())
    foreground = threading.get_ident()
    started = time.monotonic()
    try:
        result = api.get_dashboard_finance(
            period="custom", start=date(2026, 8, 2), end=date(2026, 8, 31))
        elapsed = time.monotonic() - started
        assert elapsed < 0.3
        assert result["balance"] == {}
        assert entered.wait(0.3)
        assert foreground not in calls
    finally:
        finished.set()


def test_background_429_never_sleeps_or_retries(monkeypatch, balance_state):
    response = Mock(status_code=429, headers={"Retry-After": "60"})
    request = Mock(return_value=response)
    monkeypatch.setattr("requests.Session.request", request)
    monkeypatch.setattr("wb_api.time.sleep", lambda *_: pytest.fail("429 must not sleep"))
    token_key = hashlib.sha256(b"test-token").hexdigest()
    api._finance_balance_refreshing.add(token_key)
    api._refresh_finance_balance(token_key, "test-token")
    assert request.call_count == 1
    assert request.call_args.kwargs["timeout"] == 5
    assert token_key not in api._finance_balance_refreshing
    assert api._finance_balance_cache is None
    # Failed refreshes are throttled even when another period is requested.
    assert api._finance_balance_snapshot() == {}
    assert request.call_count == 1


def test_snapshot_preserves_stale_balance_and_isolates_tokens(monkeypatch, balance_state):
    token_key = hashlib.sha256(b"test-token").hexdigest()
    api._finance_balance_cache = (token_key, time.monotonic() - 301, {"balance": 42})
    submit = Mock()
    monkeypatch.setattr(api._finance_balance_executor, "submit", submit)
    result = api._finance_balance_snapshot()
    result["balance"] = 0
    assert api._finance_balance_snapshot() == {"balance": 42}
    assert submit.call_count == 1
    monkeypatch.setattr(api, "get_token", lambda: "different-token")
    assert api._finance_balance_snapshot() == {}


def test_successful_refresh_unwraps_wb_data(monkeypatch, balance_state):
    response = Mock(status_code=200, content=b"json")
    response.json.return_value = {"data": {"balance": 42}}
    monkeypatch.setattr("requests.Session.request", Mock(return_value=response))
    token_key = hashlib.sha256(b"test-token").hexdigest()
    api._refresh_finance_balance(token_key, "test-token")
    assert api._finance_balance_snapshot() == {"balance": 42}


def test_custom_http_response_survives_wb_429(monkeypatch, balance_state):
    called = threading.Event()
    response = Mock(status_code=429, headers={"Retry-After": "60"})
    def limited_request(*args, **kwargs):
        called.set()
        return response
    sleep = Mock()
    monkeypatch.setattr("requests.Session.request", limited_request)
    monkeypatch.setattr("wb_api.time.sleep", sleep)
    monkeypatch.setattr(api, "_build_dashboard_cached", lambda *_: types.SimpleNamespace(
        financial={}, financial_products=pd.DataFrame()))
    monkeypatch.setattr(api, "read_sales_fifo_cogs", lambda *_: pd.DataFrame())
    monkeypatch.setattr(api, "read_financial_fifo_reconciliation", lambda *_: pd.DataFrame())
    started = time.monotonic()
    with TestClient(api.app, base_url="http://localhost") as client:
        result = client.get("/api/v1/dashboard/finance?period=custom&start=2026-08-03&end=2026-09-01")
    assert result.status_code == 200
    assert time.monotonic() - started < 1
    assert result.json()["balance"] == {}
    assert called.wait(0.3)
    api._finance_balance_executor.shutdown(wait=True)
    sleep.assert_not_called()

