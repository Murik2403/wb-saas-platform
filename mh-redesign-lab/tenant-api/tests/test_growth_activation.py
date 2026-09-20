import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from growth_activation import activation_summary, initialize_growth_schema, record_first_value


@pytest.fixture
def growth_db(tmp_path):
    path = tmp_path / "activation.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE sync_log(id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT, details TEXT);
            CREATE TABLE costs(nm_id INTEGER PRIMARY KEY, cost_per_wb_unit REAL, updated_at TEXT);
            CREATE TABLE financial_report(rrd_id INTEGER PRIMARY KEY, nm_id INTEGER, operation_date TEXT, quantity INTEGER, retail_amount REAL);
            INSERT INTO sync_log VALUES(1,'2026-09-18T10:00:00','2026-09-18T10:01:00','success','products: 1, orders: 2, sales: 2, financial_rows: 2, ads: 0');
            INSERT INTO costs VALUES(123,420,'2026-09-18T10:02:00');
            INSERT INTO financial_report VALUES(1,123,'2026-09-17',2,2400);
        """)
    initialize_growth_schema(path)
    return path


def test_read_only_readiness_does_not_backfill_activation(growth_db):
    result = activation_summary(growth_db, connected=True, is_demo=False, nm_id=123)
    assert result["ready_for_report"] is True
    assert result["first_value_verified"] is False
    assert result["next_step"] == "open_report"
    with sqlite3.connect(growth_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM growth_first_value").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM growth_event_outbox").fetchone()[0] == 0


def test_missing_selection_recommends_latest_sold_sku(growth_db):
    with sqlite3.connect(growth_db) as conn:
        conn.execute("INSERT INTO financial_report VALUES(2,456,'2026-09-18',1,900)")
        conn.execute("INSERT INTO financial_report VALUES(3,789,'2026-09-18',3,1200)")
    result = activation_summary(growth_db, connected=True, is_demo=False, nm_id=None)
    assert result["selected_product"]["nm_id"] is None
    assert result["recommended_product"] == {
        "nm_id": 789,
        "product_name": "",
        "supplier_article": "",
        "latest_sale_at": "2026-09-18",
    }


def test_missing_selection_does_not_recommend_non_sold_rows(growth_db):
    with sqlite3.connect(growth_db) as conn:
        conn.execute("DELETE FROM financial_report")
        conn.execute("INSERT INTO financial_report VALUES(2,456,'2026-09-18',0,0)")
    assert activation_summary(growth_db, connected=True, is_demo=False, nm_id=None)["recommended_product"] is None


@pytest.mark.parametrize("status", ["error", "partial", "running"])
def test_failed_partial_or_running_sync_does_not_count(growth_db, status):
    with sqlite3.connect(growth_db) as conn:
        conn.execute("UPDATE sync_log SET status=?", (status,))
    result = activation_summary(growth_db, connected=True, is_demo=False, nm_id=123)
    assert not result["ready_for_report"]
    assert result["next_step"] == "sync"


def test_ads_only_success_does_not_complete_full_import(growth_db):
    with sqlite3.connect(growth_db) as conn:
        conn.execute("UPDATE sync_log SET status='error'")
        conn.execute("INSERT INTO sync_log VALUES(2,'now','now','success','scope: ads, ads: 4')")
    assert not activation_summary(growth_db, connected=True, is_demo=False, nm_id=123)["ready_for_report"]


@pytest.mark.parametrize("cost", [0, -1, float("inf")])
def test_missing_or_invalid_cost_does_not_count(growth_db, cost):
    with sqlite3.connect(growth_db) as conn:
        conn.execute("UPDATE costs SET cost_per_wb_unit=?", (cost,))
    result = activation_summary(growth_db, connected=True, is_demo=False, nm_id=123)
    assert not result["ready_for_report"]
    assert result["next_step"] == "cost"


def test_cost_for_another_sku_or_no_financial_rows_does_not_count(growth_db):
    assert not activation_summary(growth_db, connected=True, is_demo=False, nm_id=456)["ready_for_report"]
    with sqlite3.connect(growth_db) as conn:
        conn.execute("DELETE FROM financial_report")
    assert activation_summary(growth_db, connected=True, is_demo=False, nm_id=123)["next_step"] == "financial_data"


def test_demo_or_disconnected_tenant_never_ready(growth_db):
    assert not activation_summary(growth_db, connected=True, is_demo=True, nm_id=123)["ready_for_report"]
    assert not activation_summary(growth_db, connected=False, is_demo=False, nm_id=123)["ready_for_report"]


def test_first_value_and_outbox_are_atomic_idempotent_and_not_backdated(growth_db):
    first = record_first_value(growth_db, nm_id=123, occurred_at="2026-09-18T11:00:00+00:00")
    repeated = record_first_value(growth_db, nm_id=123, occurred_at="2026-09-18T12:00:00+00:00")
    assert first == repeated
    assert first["occurred_at"] == "2026-09-18T11:00:00+00:00"
    with sqlite3.connect(growth_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM growth_first_value").fetchone()[0] == 1
        row = conn.execute("SELECT event_key,event_name,occurred_at,delivered_at FROM growth_event_outbox").fetchone()
        assert row == ("first_useful_report", "first_useful_report", first["occurred_at"], None)


def test_outbox_failure_rolls_back_first_value(growth_db):
    with sqlite3.connect(growth_db) as conn:
        conn.execute("CREATE TRIGGER reject_outbox BEFORE INSERT ON growth_event_outbox BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        record_first_value(growth_db, nm_id=123, occurred_at="2026-09-18T11:00:00+00:00")
    with sqlite3.connect(growth_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM growth_first_value").fetchone()[0] == 0
