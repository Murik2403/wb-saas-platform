from datetime import date
from types import SimpleNamespace

import pandas as pd

from test_display_contract import api


def test_finance_reuses_the_exact_snapshot_fifo_diagnostic(monkeypatch):
    api._clear_dashboard_caches()
    summary = {"sale_return_rows": 2, "exact_rows": 1, "exact_units": 1}
    monkeypatch.setattr(api, "_build_dashboard_cached", lambda *_: SimpleNamespace(
        financial={}, financial_products=pd.DataFrame(), financial_fifo_summary=summary))
    monkeypatch.setattr(api, "read_sales_fifo_cogs", lambda *_: pd.DataFrame())
    monkeypatch.setattr(api, "read_financial_fifo_reconciliation", lambda *_: (_ for _ in ()).throw(AssertionError("duplicate FIFO read")))
    monkeypatch.setattr(api, "_finance_balance_snapshot", lambda: {})
    result = api.get_dashboard_finance(period="custom", start=date(2026, 7, 15), end=date(2026, 10, 12))
    assert result["fifo_reconciliation"] == summary
    api._clear_dashboard_caches()


def test_monthly_cache_is_period_bound_and_write_invalidated(monkeypatch):
    api._clear_dashboard_caches()
    calls = []
    def build(start, end):
        calls.append((start, end))
        return {"months": [], "total": {}, "period": {"start": start.isoformat(), "end": end.isoformat()}}
    monkeypatch.setattr(api, "monthly_pnl", build)
    monkeypatch.setattr(api, "_tax_estimates_enabled", lambda: False)
    kw = {"period": "custom", "start": date(2026, 7, 15), "end": date(2026, 10, 12)}
    first = api.get_management_pnl_monthly(**kw)
    assert api.get_management_pnl_monthly(**kw) == first
    assert len(calls) == 1
    api.get_management_pnl_monthly(period="custom", start=date(2026, 9, 1), end=kw["end"])
    assert len(calls) == 2
    api._clear_dashboard_caches()
    api.get_management_pnl_monthly(**kw)
    assert len(calls) == 3
    api._clear_dashboard_caches()


def test_monthly_cached_base_is_not_mutated_by_tax_or_caller(monkeypatch):
    api._clear_dashboard_caches()
    base = {"months": [{"month": "2026-09"}], "total": {}}
    monkeypatch.setattr(api, "monthly_pnl", lambda *_: base)
    monkeypatch.setattr(api, "_tax_estimates_enabled", lambda: True)
    monkeypatch.setattr(api, "add_tax_block", lambda result: result.update(tax={"estimated": True}))
    kw = {"period": "custom", "start": date(2026, 9, 1), "end": date(2026, 9, 30)}
    first = api.get_management_pnl_monthly(**kw)
    first["months"].clear()
    monkeypatch.setattr(api, "_tax_estimates_enabled", lambda: False)
    second = api.get_management_pnl_monthly(**kw)
    assert second["months"] == [{"month": "2026-09"}]
    assert "tax" not in second and "tax" not in base
    assert second["tax_estimates_enabled"] is False
    api._clear_dashboard_caches()
