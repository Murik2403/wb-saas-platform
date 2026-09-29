"""Focused safety contracts for raw-material planning."""
from __future__ import annotations

import sys
from pathlib import Path

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


def test_partial_production_task_consumes_only_remaining_planned_units():
    result = build_supply_preview(snapshot(
        tasks=[{"task_key": "partial", "task_date": "2026-09-12", "nm_id": 1, "chrt_id": 0, "planned_units": 10, "actual_units": 4, "status": "В работе"}],
        boms=[{"id": 1, "nm_id": 1, "chrt_id": 0, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "quantity_per_unit": 2, "unit": "м"}]}],
        inventory=[{"material_key": "a", "material_name": "A", "balance_known": 1, "full_rolls": 0, "partial_meters": 0, "roll_length": 1, "unit": "м", "tracking_mode": "quantity"}],
    ))
    assert result["lines"][0]["required_base_qty"] == 12


class SupplyPlanningDatabaseTests(DbTestCase):
    def test_concurrent_confirmation_creates_one_order(self):
        from concurrent.futures import ThreadPoolExecutor
        from db.core import connect
        with connect() as conn:
            self._seed_plan(conn)
            fingerprint = build_supply_preview(load_supply_plan_snapshot(conn, "2026-09-01", "2026-09-30"))["preview_fingerprint"]
        def confirm_once():
            with connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = build_supply_preview(load_supply_plan_snapshot(conn, "2026-09-01", "2026-09-30"))
                return confirm_supply_plan(conn, "concurrent", "same-body", fingerprint, current,
                    [{"material_key": "fabric-a", "unit": "м", "quantity": 1, "supplier_id": 1, "expected_date": "2026-09-10"}])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: confirm_once(), range(2)))
        assert sorted(result["replayed"] for result in results) == [False, True]
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM procurement_orders").fetchone()[0] == 1

    def test_duplicate_material_confirmation_rolls_back(self):
        from db.core import connect
        with connect() as conn:
            self._seed_plan(conn)
            preview = build_supply_preview(load_supply_plan_snapshot(conn, "2026-09-01", "2026-09-30"))
            edit = {"material_key": "fabric-a", "unit": "м", "quantity": 1, "supplier_id": 1, "expected_date": "2026-09-10"}
            with self.assertRaisesRegex(ValueError, "повторно"):
                confirm_supply_plan(conn, "duplicate", "hash", preview["preview_fingerprint"], preview, [edit, edit])
            assert conn.execute("SELECT COUNT(*) FROM procurement_orders").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM supply_plan_confirmations").fetchone()[0] == 0

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

def test_arrival_between_tasks_covers_later_demand_but_not_early_shortage():
    source = snapshot(
        tasks=[{"task_key": "early", "task_date": "2026-09-10", "nm_id": 1, "planned_units": 2},
               {"task_key": "late", "task_date": "2026-09-20", "nm_id": 1, "planned_units": 8}],
        boms=[{"id": 1, "nm_id": 1, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "unit": "кг", "quantity_per_unit": 1}]}],
        inventory=[{"material_key": "a", "unit": "кг", "balance_known": 1, "partial_meters": 0}],
        open_orders=[{"material_key": "a", "unit": "кг", "quantity": 8, "posted_quantity": 0, "status": "В пути", "expected_date": "2026-09-15"}],
    )
    line = build_supply_preview(source)["lines"][0]
    assert line["shortage_base_qty"] == 2
    assert line["open_order_covered_base_qty"] == 8


def test_canceled_order_residual_does_not_block_or_cover_demand():
    source = snapshot(
        tasks=[{"task_key": "t", "task_date": "2026-09-10", "nm_id": 1, "planned_units": 2}],
        boms=[{"id": 1, "nm_id": 1, "version": 1, "materials": [{"material_key": "a", "material_name": "A", "unit": "кг", "quantity_per_unit": 1}]}],
        inventory=[{"material_key": "a", "unit": "кг", "balance_known": 1, "partial_meters": 0}],
        open_orders=[{"material_key": "a", "unit": "кг", "quantity": 8, "status": "Отменено", "expected_date": "2026-09-05"}],
    )
    result = build_supply_preview(source)
    assert result["blockers"] == []
    assert result["lines"][0]["shortage_base_qty"] == 2
