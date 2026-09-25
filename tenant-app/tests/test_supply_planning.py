"""Focused safety contracts for raw-material planning."""
from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

import pytest
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from db.supply_planning import build_supply_preview
from db.supply_planning import confirm_supply_plan, load_supply_plan_snapshot
from base import DbTestCase


def snapshot(**overrides):
    base = {
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
        "tasks": [],
        "variants": [],
        "boms": [],
        "inventory": [],
        "open_orders": [],
        "suppliers": [],
    }
    return {**base, **overrides}


def test_empty_snapshot_has_no_lines_or_blockers():
    result = build_supply_preview(snapshot())
    assert result["lines"] == []
    assert result["blockers"] == []
    assert result["preview_fingerprint"]


def test_task_multiplies_exact_bom_and_aggregates_materials_with_traceability():
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "task-1", "task_date": "2026-09-12", "nm_id": 10, "chrt_id": 0, "planned_units": 3, "status": "Не начато"}],
        boms=[{"id": 1, "nm_id": 10, "chrt_id": 0, "version": 2, "materials": [
            {"material_key": "fabric-a", "material_name": "Ткань A", "quantity_per_unit": 2, "unit": "м"},
            {"material_key": "dye-b", "material_name": "Краситель B", "quantity_per_unit": .5, "unit": "кг"},
        ]}],
        inventory=[{"material_key": "fabric-a", "material_name": "Ткань A", "balance_known": 1, "full_rolls": 0, "partial_meters": 0, "roll_length": 25, "unit": "м", "tracking_mode": "packaged"},
                   {"material_key": "dye-b", "material_name": "Краситель B", "balance_known": 1, "full_rolls": 0, "partial_meters": 0, "roll_length": 1, "unit": "кг", "tracking_mode": "quantity"}],
    ))
    lines = {line["material_key"]: line for line in result["lines"]}
    assert lines["fabric-a"]["required_base_qty"] == 6
    assert lines["dye-b"]["required_base_qty"] == 1.5
    assert lines["fabric-a"]["source_refs"][0]["bom_version"] == 2


def test_multiple_skus_aggregate_same_material_and_keep_all_variant_provenance():
    result = build_supply_preview(snapshot(
        tasks=[
            {"task_key": "task-a", "task_date": "2026-09-12", "nm_id": 10, "chrt_id": 0, "planned_units": 3, "status": "Не начато"},
            {"task_key": "task-b", "task_date": "2026-09-13", "nm_id": 20, "chrt_id": 0, "planned_units": 2, "status": "Не начато"},
        ],
        variants=[{"nm_id": 10, "chrt_id": 101}, {"nm_id": 10, "chrt_id": 102}],
        boms=[
            {"id": 1, "nm_id": 10, "chrt_id": 101, "version": 2, "materials": [{"material_key": "fabric-a", "material_name": "Ткань A", "quantity_per_unit": 2, "unit": "м"}]},
            {"id": 2, "nm_id": 10, "chrt_id": 102, "version": 1, "materials": [{"material_key": "fabric-a", "material_name": "Ткань A", "quantity_per_unit": 2, "unit": "м"}]},
            {"id": 3, "nm_id": 20, "chrt_id": 0, "version": 4, "materials": [{"material_key": "fabric-a", "material_name": "Ткань A", "quantity_per_unit": 1, "unit": "М"}]},
        ],
    ))
    assert len(result["lines"]) == 1
    line = result["lines"][0]
    assert line["required_base_qty"] == 8
    refs = {(ref["task_key"], ref["nm_id"], ref["chrt_id"], ref["bom_version_id"]) for ref in line["source_refs"]}
    assert refs == {("task-a", 10, 101, 1), ("task-a", 10, 102, 2), ("task-b", 20, 0, 3)}
    assert set(line["bom_version_ids"]) == {1, 2, 3}


def test_ambiguous_variant_demand_blocks_until_complete_explicit_allocation():
    source = snapshot(
        tasks=[{"task_key": "task-variant", "task_date": "2026-09-12", "nm_id": 20, "chrt_id": 0, "planned_units": 5, "status": "Не начато"}],
        variants=[{"nm_id": 20, "chrt_id": 201}, {"nm_id": 20, "chrt_id": 202}],
        boms=[{"id": 1, "nm_id": 20, "chrt_id": 201, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 1, "unit": "м"}]},
              {"id": 2, "nm_id": 20, "chrt_id": 202, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 2, "unit": "м"}]}],
    )
    blocked = build_supply_preview(source)
    assert any(b["code"] == "ambiguous_variant_demand" for b in blocked["blockers"])
    allocated = build_supply_preview(source, [{"task_key": "task-variant", "chrt_id": 201, "units": 2}, {"task_key": "task-variant", "chrt_id": 202, "units": 3}])
    assert not any(b["code"] == "ambiguous_variant_demand" for b in allocated["blockers"])
    assert {line["material_key"]: line["required_base_qty"] for line in allocated["lines"]}["a"] == 8
    short = build_supply_preview(source, [{"task_key": "task-variant", "chrt_id": 201, "units": 2}])
    assert any(b["code"] == "incomplete_variant_allocation" for b in short["blockers"])
    over = build_supply_preview(source, [{"task_key": "task-variant", "chrt_id": 201, "units": 3}, {"task_key": "task-variant", "chrt_id": 202, "units": 3}])
    assert any(b["code"] == "incomplete_variant_allocation" for b in over["blockers"])
    assert over["lines"] == []


def test_variant_bom_uses_latest_exact_version_or_explicit_base_fallback():
    task = {"task_key": "variant", "task_date": "2026-09-12", "nm_id": 30, "chrt_id": 301, "planned_units": 2, "status": "Не начато"}
    common = [{"material_key": "fabric-a", "material_name": "Ткань A", "quantity_per_unit": 1, "unit": "м"}]
    exact = build_supply_preview(snapshot(tasks=[task], boms=[
        {"id": 1, "nm_id": 30, "chrt_id": 301, "version": 1, "materials": [{**common[0], "quantity_per_unit": 3}]},
        {"id": 2, "nm_id": 30, "chrt_id": 301, "version": 2, "materials": common},
    ]))
    assert exact["lines"][0]["required_base_qty"] == 2
    assert exact["lines"][0]["source_refs"][0]["bom_version_id"] == 2
    fallback = build_supply_preview(snapshot(tasks=[task], boms=[{"id": 3, "nm_id": 30, "chrt_id": 0, "version": 1, "materials": common}]))
    assert fallback["lines"][0]["fallback_to_base_bom"] is True
    assert any(w["code"] == "fallback_to_base_bom" for w in fallback["warnings"])
    missing = build_supply_preview(snapshot(tasks=[task]))
    assert any(b["code"] == "missing_bom" for b in missing["blockers"])
    assert missing["lines"] == []
    changed_version = build_supply_preview(snapshot(tasks=[task], boms=[{"id": 2, "nm_id": 30, "chrt_id": 301, "version": 3, "materials": common}]))
    assert changed_version["preview_fingerprint"] != exact["preview_fingerprint"]


def test_same_material_key_in_distinct_units_never_merges():
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "t", "task_date": "2026-09-12", "nm_id": 1, "chrt_id": 0, "planned_units": 2, "status": "Не начато"}],
        boms=[{"id": 1, "nm_id": 1, "chrt_id": 0, "version": 1, "materials": [
            {"material_key": "shared", "material_name": "Материал", "quantity_per_unit": 2, "unit": "м"},
            {"material_key": "shared", "material_name": "Материал", "quantity_per_unit": 3, "unit": "кг"},
        ]}],
    ))
    assert {(line["material_key"], line["unit"], line["required_base_qty"]) for line in result["lines"]} == {("shared", "м", 4), ("shared", "кг", 6)}


def test_unknown_inventory_does_not_reduce_shortage_and_packaged_quantity_rounds_up():
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "t", "task_date": "2026-09-12", "nm_id": 1, "chrt_id": 0, "planned_units": 3, "status": "Не начато"}],
        boms=[{"id": 1, "nm_id": 1, "chrt_id": 0, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 2, "unit": "м"}]}],
        inventory=[{"material_key": "a", "material_name": "A", "balance_known": 0, "full_rolls": 2, "partial_meters": 1, "roll_length": 10, "unit": "м", "tracking_mode": "packaged"}],
    ))
    line = result["lines"][0]
    assert line["required_base_qty"] == 6
    assert line["physical_available_base_qty"] is None
    assert line["suggested_purchase_qty"] == 1
    assert line["pack_size_base_qty"] == 10
    assert any(b["code"] == "unknown_inventory_balance" for b in result["blockers"])


def test_fifo_physical_balance_mismatch_is_a_blocker_and_not_available_coverage():
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "t", "task_date": "2026-09-12", "nm_id": 1, "chrt_id": 0, "planned_units": 3, "status": "Не начато"}],
        boms=[{"id": 1, "nm_id": 1, "chrt_id": 0, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 2, "unit": "м"}]}],
        inventory=[{"material_key": "a", "material_name": "A", "balance_known": 1, "full_rolls": 0, "partial_meters": 4, "roll_length": 1, "unit": "м", "tracking_mode": "quantity"}],
        fifo_inventory=[{"material_key": "a", "remaining_qty": 2}],
    ))
    assert result["lines"][0]["physical_available_base_qty"] == 0
    assert any(b["code"] == "fifo_stock_mismatch" for b in result["blockers"])


def test_packaged_inventory_without_a_valid_pack_size_blocks_order_rounding():
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "t", "task_date": "2026-09-12", "nm_id": 1, "chrt_id": 0, "planned_units": 3, "status": "Не начато"}],
        boms=[{"id": 1, "nm_id": 1, "chrt_id": 0, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 2, "unit": "м"}]}],
        inventory=[{"material_key": "a", "material_name": "A", "balance_known": 1, "full_rolls": 0, "partial_meters": 0, "roll_length": 0, "unit": "м", "tracking_mode": "packaged"}],
    ))
    assert any(blocker["code"] == "invalid_pack_size" for blocker in result["blockers"])
    assert result["lines"][0]["pack_size_base_qty"] is None


def test_open_partial_po_only_covers_unreceived_amount_arriving_before_need_date():
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "t", "task_date": "2026-09-12", "nm_id": 1, "chrt_id": 0, "planned_units": 10, "status": "Не начато"}],
        boms=[{"id": 1, "nm_id": 1, "chrt_id": 0, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 2, "unit": "кг"}]}],
        inventory=[{"material_key": "a", "material_name": "A", "balance_known": 1, "full_rolls": 0, "partial_meters": 3, "roll_length": 1, "unit": "кг", "tracking_mode": "quantity"}],
        fifo_inventory=[{"material_key": "a", "remaining_qty": 3}],
        open_orders=[{"procurement_type": "Сырьё", "status": "Частично получено", "expected_date": "2026-09-10", "item_type": "Сырьё", "material_key": "a", "quantity": 10, "posted_quantity": 4, "unit": "кг"},
                      {"procurement_type": "Сырьё", "status": "В пути", "expected_date": "2026-09-20", "item_type": "Сырьё", "material_key": "a", "quantity": 10, "posted_quantity": 0, "unit": "кг"}],
    ))
    line = result["lines"][0]
    assert line["required_base_qty"] == 20
    assert line["open_order_covered_base_qty"] == 6
    assert line["shortage_base_qty"] == 11


def test_open_partially_received_roll_order_converts_remaining_rolls_to_base_unit():
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "t", "task_date": "2026-09-12", "nm_id": 1, "chrt_id": 0, "planned_units": 15, "status": "Не начато"}],
        boms=[{"id": 1, "nm_id": 1, "chrt_id": 0, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 2, "unit": "м"}]}],
        inventory=[{"material_key": "a", "material_name": "A", "balance_known": 1, "full_rolls": 0, "partial_meters": 0, "roll_length": 10, "unit": "м", "tracking_mode": "packaged"}],
        open_orders=[{"order_id": 1, "procurement_type": "Сырьё", "status": "Частично получено", "expected_date": "2026-09-10", "item_type": "Сырьё", "material_key": "a", "quantity": 3, "posted_quantity": 1, "unit": "рулон", "roll_length": 10}],
        fifo_inventory=[{"material_key": "a", "remaining_qty": 0}],
    ))
    line = result["lines"][0]
    assert line["required_base_qty"] == 30
    assert line["open_order_covered_base_qty"] == 20
    assert line["shortage_base_qty"] == 10


def test_invalid_open_roll_conversion_blocks_instead_of_assuming_coverage():
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "t", "task_date": "2026-09-12", "nm_id": 1, "chrt_id": 0, "planned_units": 5, "status": "Не начато"}],
        boms=[{"id": 1, "nm_id": 1, "chrt_id": 0, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 2, "unit": "м"}]}],
        inventory=[{"material_key": "a", "material_name": "A", "balance_known": 1, "full_rolls": 0, "partial_meters": 0, "roll_length": 10, "unit": "м", "tracking_mode": "packaged"}],
        open_orders=[{"order_id": 1, "procurement_type": "Сырьё", "status": "В пути", "expected_date": "2026-09-10", "item_type": "Сырьё", "material_key": "a", "quantity": 5, "posted_quantity": 0, "unit": "рулон", "roll_length": 0}],
    ))
    assert result["lines"][0]["open_order_covered_base_qty"] == 0
    assert any(b["code"] == "invalid_open_order_pack" for b in result["blockers"])


def test_open_order_with_unmapped_unit_blocks_instead_of_silent_omission():
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "t", "task_date": "2026-09-12", "nm_id": 1, "chrt_id": 0, "planned_units": 5, "status": "Не начато"}],
        boms=[{"id": 1, "nm_id": 1, "chrt_id": 0, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 2, "unit": "м"}]}],
        inventory=[{"material_key": "a", "material_name": "A", "balance_known": 1, "full_rolls": 0, "partial_meters": 0, "roll_length": 10, "unit": "м", "tracking_mode": "packaged"}],
        open_orders=[{"order_id": 2, "procurement_type": "Сырьё", "status": "В пути", "expected_date": "2026-09-10", "item_type": "Сырьё", "material_key": "a", "quantity": 5, "posted_quantity": 0, "unit": "упаковка", "roll_length": 10}],
    ))
    assert result["lines"][0]["open_order_covered_base_qty"] == 0
    assert any(b["code"] == "open_order_unit_mismatch" for b in result["blockers"])


@pytest.mark.parametrize(("status", "posted", "expected_coverage", "expected_blocker"), [
    ("Заказано", 0, 5, None),
    ("Получено", 5, 0, None),
    ("Отменено", 5, 0, None),
    ("Отменено", 0, 0, "closed_order_residual"),
    ("Статус миграции", 0, 0, "unknown_open_order_status"),
])
def test_open_order_status_matrix(status, posted, expected_coverage, expected_blocker):
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "t", "task_date": "2026-09-12", "nm_id": 1, "chrt_id": 0, "planned_units": 5, "status": "Не начато"}],
        boms=[{"id": 1, "nm_id": 1, "chrt_id": 0, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 2, "unit": "м"}]}],
        inventory=[{"material_key": "a", "material_name": "A", "balance_known": 1, "full_rolls": 0, "partial_meters": 0, "roll_length": 1, "unit": "м", "tracking_mode": "quantity"}],
        open_orders=[{"order_id": 17, "status": status, "expected_date": "2026-09-10", "material_key": "a", "quantity": 5, "posted_quantity": posted, "unit": "м"}],
    ))
    assert result["lines"][0]["open_order_covered_base_qty"] == expected_coverage
    if expected_blocker:
        assert any(blocker["code"] == expected_blocker for blocker in result["blockers"])
    else:
        assert not result["blockers"]


def test_unknown_eta_does_not_cover_demand_and_raises_timing_warning():
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "t", "task_date": "2026-09-12", "nm_id": 1, "chrt_id": 0, "planned_units": 5, "status": "Не начато"}],
        boms=[{"id": 1, "nm_id": 1, "chrt_id": 0, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 2, "unit": "м"}]}],
        inventory=[{"material_key": "a", "material_name": "A", "balance_known": 1, "full_rolls": 0, "partial_meters": 0, "roll_length": 1, "unit": "м", "tracking_mode": "quantity"}],
        open_orders=[{"order_id": 18, "status": "В пути", "expected_date": "", "material_key": "a", "quantity": 5, "posted_quantity": 1, "unit": "м"}],
    ))
    assert result["lines"][0]["open_order_covered_base_qty"] == 0
    warning = next(w for w in result["warnings"] if w["code"] == "open_order_eta_risk")
    assert warning["expected_date"] is None


def test_closed_order_with_legacy_incompatible_unit_does_not_block_preview_when_fully_received():
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "t", "task_date": "2026-09-12", "nm_id": 1, "chrt_id": 0, "planned_units": 1, "status": "Не начато"}],
        boms=[{"id": 1, "nm_id": 1, "chrt_id": 0, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 2, "unit": "м"}]}],
        inventory=[{"material_key": "a", "material_name": "A", "balance_known": 1, "full_rolls": 0, "partial_meters": 0, "roll_length": 1, "unit": "м", "tracking_mode": "quantity"}],
        open_orders=[{"order_id": 19, "status": "Получено", "expected_date": "2026-09-10", "material_key": "a", "quantity": 5, "posted_quantity": 5, "unit": "упаковка"}],
    ))
    assert result["blockers"] == []
    assert result["lines"][0]["open_order_covered_base_qty"] == 0


def test_partial_production_task_consumes_only_remaining_planned_units():
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "partial", "task_date": "2026-09-12", "nm_id": 1, "chrt_id": 0, "planned_units": 10, "actual_units": 4, "status": "В работе"}],
        boms=[{"id": 1, "nm_id": 1, "chrt_id": 0, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 2, "unit": "м"}]}],
        inventory=[{"material_key": "a", "material_name": "A", "balance_known": 1, "full_rolls": 0, "partial_meters": 0, "roll_length": 1, "unit": "м", "tracking_mode": "quantity"}],
    ))
    assert result["lines"][0]["required_base_qty"] == 12


class SupplyPlanningDatabaseTests(DbTestCase):
    def _seed_plan(self, conn):
        conn.execute("INSERT INTO execution_tasks(task_key,task_type,task_date,nm_id,chrt_id,planned_units,actual_units,status,updated_at) VALUES ('t1','production','2026-09-12',10,0,3,0,'Не начато','2026-09-01')")
        conn.execute("INSERT INTO manufacturing_boms(nm_id,chrt_id,version,materials_json,operations_json,packaging_per_unit,other_per_unit,note,created_at) VALUES (10,0,1,?, '[]',0,0,'','2026-09-01')", ('[{"material_key":"fabric-a","material_name":"Ткань A","quantity_per_unit":2,"unit":"м"}]',))
        conn.execute("INSERT INTO material_inventory_color(material_key,material_name,balance_known,full_rolls,partial_meters,roll_length,unit,tracking_mode,updated_at) VALUES ('fabric-a','Ткань A',1,0,0,25,'м','packaged','2026-09-01')")
        conn.execute("INSERT INTO suppliers(name,default_currency,lead_time_days,active,created_at,updated_at) VALUES ('Поставщик','RUB',5,1,'2026-09-01','2026-09-01')")

    def test_db_snapshot_and_preview_do_not_write(self):
        from db.core import connect
        with connect() as conn:
            self._seed_plan(conn)
            before = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("procurement_orders", "procurement_items", "inventory_movements", "material_cost_layers", "execution_tasks"))
            loaded = load_supply_plan_snapshot(conn, "2026-09-01", "2026-09-30")
            result = build_supply_preview(loaded)
            after = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("procurement_orders", "procurement_items", "inventory_movements", "material_cost_layers", "execution_tasks"))
        assert result["lines"][0]["required_base_qty"] == 6
        assert before == after == (0, 0, 0, 0, 1)

    def test_additive_confirmation_ledger_migration_preserves_existing_procurement(self):
        from db.core import connect, init_db
        with connect() as conn:
            conn.execute("INSERT INTO procurement_orders(order_number,procurement_type,supplier_name,status,order_date,currency,exchange_rate,note,created_at,updated_at) VALUES ('LEGACY-1','Сырьё','Old supplier','Запланировано','2026-09-01','RUB',1,'keep','2026-09-01','2026-09-01')")
            conn.execute("INSERT INTO procurement_items(order_id,item_type,material_key,material_name,quantity,unit,roll_length,created_at,updated_at) VALUES (1,'Сырьё','legacy','Legacy',4,'м',1,'2026-09-01','2026-09-01')")
        init_db()
        with connect() as conn:
            order = conn.execute("SELECT order_number,supplier_name,note FROM procurement_orders WHERE id=1").fetchone()
            item = conn.execute("SELECT material_key,quantity,unit FROM procurement_items WHERE order_id=1").fetchone()
            assert tuple(order) == ("LEGACY-1", "Old supplier", "keep")
            assert tuple(item) == ("legacy", 4.0, "м")
            assert conn.execute("SELECT COUNT(*) FROM supply_plan_confirmations").fetchone()[0] == 0

    def test_confirmation_is_atomic_and_same_request_replays(self):
        from db.core import connect
        with connect() as conn:
            self._seed_plan(conn)
            preview = build_supply_preview(load_supply_plan_snapshot(conn, "2026-09-01", "2026-09-30"))
            edit = {"material_key": "fabric-a", "unit": "м", "quantity": 1, "supplier_id": 1, "expected_date": "2026-09-10"}
            first = confirm_supply_plan(conn, "00000000-0000-4000-8000-000000000001", "request-hash-1", preview["preview_fingerprint"], preview, [edit])
            second = confirm_supply_plan(conn, "00000000-0000-4000-8000-000000000001", "request-hash-1", preview["preview_fingerprint"], preview, [edit])
            assert first["order_ids"] == second["order_ids"]
            assert second["replayed"] is True
            assert conn.execute("SELECT COUNT(*) FROM procurement_orders").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM procurement_items").fetchone()[0] == 1
            item = conn.execute("SELECT quantity,unit,roll_length FROM procurement_items").fetchone()
            assert tuple(item) == (1.0, "рулон", 25.0)
            with self.assertRaisesRegex(ValueError, "idempotency_conflict"):
                confirm_supply_plan(conn, "00000000-0000-4000-8000-000000000001", "different-hash", preview["preview_fingerprint"], preview, [edit])
            assert conn.execute("SELECT COUNT(*) FROM procurement_orders").fetchone()[0] == 1

    def test_confirmation_rejects_stale_fingerprint_before_writing(self):
        from db.core import connect
        with connect() as conn:
            self._seed_plan(conn)
            preview = build_supply_preview(load_supply_plan_snapshot(conn, "2026-09-01", "2026-09-30"))
            with self.assertRaisesRegex(ValueError, "stale_preview"):
                confirm_supply_plan(conn, "00000000-0000-4000-8000-000000000002", "hash", "stale", preview, [{"material_key": "fabric-a", "unit": "м", "quantity": 1, "supplier_id": 1, "expected_date": "2026-09-10"}])
            assert conn.execute("SELECT COUNT(*) FROM procurement_orders").fetchone()[0] == 0

    def test_second_supplier_failure_rolls_back_all_orders_items_and_idempotency(self):
        from db.core import connect
        with connect() as conn:
            self._seed_plan(conn)
            conn.execute("INSERT INTO manufacturing_boms(nm_id,chrt_id,version,materials_json,operations_json,packaging_per_unit,other_per_unit,note,created_at) VALUES (10,0,2,?, '[]',0,0,'','2026-09-02')", ('[{"material_key":"fabric-a","material_name":"Ткань A","quantity_per_unit":2,"unit":"м"},{"material_key":"dye-b","material_name":"Краситель B","quantity_per_unit":1,"unit":"кг"}]',))
            conn.execute("INSERT INTO material_inventory_color(material_key,material_name,balance_known,full_rolls,partial_meters,roll_length,unit,tracking_mode,updated_at) VALUES ('dye-b','Краситель B',1,0,0,1,'кг','quantity','2026-09-01')")
            conn.execute("INSERT INTO suppliers(name,default_currency,lead_time_days,active,created_at,updated_at) VALUES ('Поставщик 2','RUB',3,1,'2026-09-01','2026-09-01')")
            preview = build_supply_preview(load_supply_plan_snapshot(conn, "2026-09-01", "2026-09-30"))
            edits = [
                {"material_key": "fabric-a", "unit": "м", "quantity": 1, "supplier_id": 1, "expected_date": "2026-09-10"},
                {"material_key": "dye-b", "unit": "кг", "quantity": 3, "supplier_id": 2, "expected_date": "2026-09-10"},
            ]
            conn.execute("CREATE TRIGGER fail_second_supplier BEFORE INSERT ON procurement_orders WHEN NEW.supplier_id=2 BEGIN SELECT RAISE(ABORT,'injected second supplier failure'); END")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected second supplier failure"):
                confirm_supply_plan(conn, "00000000-0000-4000-8000-000000000103", "multi-supplier-request", preview["preview_fingerprint"], preview, edits)
            assert conn.execute("SELECT COUNT(*) FROM procurement_orders").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM procurement_items").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM supply_plan_confirmations").fetchone()[0] == 0
