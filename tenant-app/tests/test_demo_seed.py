from __future__ import annotations

from datetime import timedelta

import pytest

import db.core as core
import seed_demo_data


def _snapshot() -> dict[str, list[tuple]]:
    queries = {
        "procurement_orders": "SELECT source_key,status,order_date,received_date FROM procurement_orders ORDER BY source_key",
        "material_cost_layers": "SELECT source_type,source_date,original_meters,unit_cost_rub_m,status FROM material_cost_layers ORDER BY source_date,original_meters",
        "manufacturing_releases": "SELECT source_key,quantity,production_date,packaging_cost_rub,total_cost_rub,status FROM manufacturing_releases ORDER BY source_key",
        "finished_goods_cost_layers": "SELECT source_type,source_date,original_units,unit_cost_rub,status FROM finished_goods_cost_layers ORDER BY source_type,source_date,original_units",
        "inventory_movements": "SELECT movement_type,movement_date,quantity,goods_cost_rub,material_cost_rub,status FROM inventory_movements ORDER BY movement_type,movement_date,quantity",
        "manual_expenses": "SELECT nm_id,article,amount_rub,period_start,period_end,note FROM manual_expenses ORDER BY note",
        "sales_fifo_events": "SELECT sale_record_id,srid,event_date,event_type,fifo_cost_rub,status,note FROM sales_fifo_events ORDER BY sale_record_id",
    }
    result = {}
    with core.connect() as conn:
        for table, query in queries.items():
            result[table] = [tuple(row) for row in conn.execute(query)]
    return result


def test_demo_seed_fails_closed_without_explicit_demo_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "must-not-be-created.sqlite3")
    monkeypatch.delenv("WB_SAAS_IS_DEMO", raising=False)
    monkeypatch.delenv("WB_SAAS_TENANT_SLUG", raising=False)

    with pytest.raises(RuntimeError, match="isolated demo tenant"):
        seed_demo_data.main()
    assert not core.DB_PATH.exists()


def test_demo_seed_builds_rolling_fifo_example_idempotently(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "demo.sqlite3")
    monkeypatch.setenv("WB_SAAS_IS_DEMO", "1")
    monkeypatch.setenv("WB_SAAS_TENANT_SLUG", "demo")

    seed_demo_data.main()
    first = _snapshot()
    seed_demo_data.main()
    second = _snapshot()

    assert first == second
    with core.connect() as conn:
        scenario_order = conn.execute(
            "SELECT id,status,order_date,received_date FROM procurement_orders WHERE source_key=?",
            ("demo-fifo-learning-procurement-v1",),
        ).fetchone()
        assert scenario_order is not None
        assert scenario_order["status"] == "Получено"
        assert scenario_order["order_date"] == (seed_demo_data.TODAY - timedelta(days=32)).isoformat()
        batch = conn.execute(
            "SELECT production_date,packaging_cost_rub,total_cost_rub FROM manufacturing_releases WHERE source_key=?",
            ("demo-fifo-learning-release-v1",),
        ).fetchone()
        assert batch is not None and batch["packaging_cost_rub"] == 120
        assert batch["production_date"] == (seed_demo_data.TODAY - timedelta(days=24)).isoformat()
        assert batch["total_cost_rub"] > 0
        rows = conn.execute(
            "SELECT event_type,status,fifo_cost_rub,matched_sale_event_id FROM sales_fifo_events WHERE srid='DEMO-FIFO-LEARNING-001' ORDER BY event_date,event_type"
        ).fetchall()
        assert len(rows) == 2
        assert all(row["status"] == "applied" and row["fifo_cost_rub"] > 0 for row in rows)
        assert rows[1]["matched_sale_event_id"] is not None
        expense = conn.execute("SELECT amount_rub FROM manual_expenses WHERE note LIKE 'DEMO-FIFO-CASE:%'").fetchall()
        assert len(expense) == 1 and expense[0][0] == 120
