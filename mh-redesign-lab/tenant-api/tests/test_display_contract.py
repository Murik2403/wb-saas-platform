import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR.parents[1] / "tenant-app"))
sys.path.insert(0, str(API_DIR))
spec = importlib.util.spec_from_file_location("redesign_display_test_api", API_DIR / "api_server.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


def test_inventory_keeps_quantity_when_coverage_unknown():
    data = SimpleNamespace(products=pd.DataFrame([{"Артикул продавца": "test", "Остаток": 66, "Запас, дней": None, "Маржа до комиссий WB": 1234.5}]))
    row = api._product_inventory(data)[0]
    assert row["stock"] == 66
    assert row["stock_days"] is None
    assert row["signal"] == "Недостаточно продаж для прогноза"
    assert row["margin_rub"] == 1234.5


def test_inventory_zero_coverage_is_not_missing():
    data = SimpleNamespace(products=pd.DataFrame([{"Остаток": 0, "Запас, дней": 0}]))
    row = api._product_inventory(data)[0]
    assert row["stock"] == 0
    assert row["stock_days"] == 0
    assert row["tone"] == "danger"


def setup_sync(monkeypatch, tmp_path, rows, connected):
    path = tmp_path / "test.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE sync_log(id INTEGER PRIMARY KEY, status TEXT, finished_at TEXT, details TEXT)")
        con.executemany("INSERT INTO sync_log(id,status,finished_at) VALUES(?,?,?)", rows)
    monkeypatch.setattr(api, "DB_PATH", path)
    monkeypatch.setattr(api, "get_token", lambda: "test-only" if connected else None)
    monkeypatch.setattr(api, "load_settings", lambda: {"sync_interval_minutes": 30})


def test_no_import_is_not_fresh(monkeypatch, tmp_path):
    setup_sync(monkeypatch, tmp_path, [], True)
    status = api.get_data_status()
    assert status["state"] == "stale"
    assert status["updated_at"] is None


def test_failed_run_does_not_advance_data_timestamp(monkeypatch, tmp_path):
    setup_sync(monkeypatch, tmp_path, [(1, "success", "2026-08-23T18:15:54"), (2, "error", "2026-09-09T12:00:00")], False)
    status = api.get_data_status()
    assert status["connected"] is False
    assert status["updated_at"] == "2026-08-23T18:15:54+03:00"
    assert status["state"] == "stale"


def test_malformed_timestamp_is_not_fresh(monkeypatch, tmp_path):
    setup_sync(monkeypatch, tmp_path, [(1, "success", "bad-date")], True)
    assert api.get_data_status()["state"] == "stale"


def test_partial_import_advances_timestamp_without_claiming_freshness(monkeypatch, tmp_path):
    setup_sync(monkeypatch, tmp_path, [(1, "success", "2026-08-23T18:15:54"), (2, "partial", "2026-09-09T20:00:00")], True)
    status = api.get_data_status()
    assert status["partial"] is True
    assert status["state"] == "stale"
    assert status["updated_at"] == "2026-09-09T20:00:00+03:00"
