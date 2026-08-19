from __future__ import annotations

import json
import math
import sqlite3
import hashlib
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterable

import pandas as pd

from config import DB_PATH

from .core import connect


def post_dispatches(task_keys: Iterable[str] | None = None) -> dict[str, Any]:
    from .core import connect
    from .fifo_finished_goods import _ensure_finished_goods_capacity, _move_finished_goods_fifo
    from .production import _active_movement, _apply_pipeline_delta, _movement_date_text, _next_event_key
    result: dict[str, Any] = {"posted": 0, "skipped": 0, "errors": [], "units": 0, "cost_rub": 0.0}
    keys = [str(k) for k in (task_keys or []) if str(k).strip()]
    with connect() as conn:
        sql = """
            SELECT * FROM execution_tasks
             WHERE task_type='dispatch' AND status='Отгружено' AND actual_units>0
        """
        params: list[Any] = []
        if keys:
            sql += f" AND task_key IN ({','.join('?' for _ in keys)})"
            params.extend(keys)
        sql += " ORDER BY nm_id, task_key"
        tasks = conn.execute(sql, params).fetchall()
        for task in tasks:
            source_key = str(task["task_key"])
            if _active_movement(conn, "dispatch", source_key):
                result["skipped"] += 1
                continue
            qty = max(0, int(task["actual_units"] or 0))
            route = str(task["route"] or "")
            if route not in {"FBS", "Ускоренная FBO", "Стандартная FBO"}:
                result["errors"].append(f"{task['supplier_article']}: не выбран маршрут.")
                continue
            dispatch_date = _movement_date_text(task["dispatch_date"]) or datetime.now().date().isoformat()
            expected = _movement_date_text(task["expected_arrival_date"])
            inbound_delta = 0 if route == "FBS" else qty
            status = "closed" if route == "FBS" else "open"
            conn.execute("SAVEPOINT post_dispatch_task")
            try:
                _ensure_finished_goods_capacity(
                    conn, int(task["nm_id"] or 0), str(task["supplier_article"] or ""),
                    str(task["product_name"] or ""), "ready", qty,
                )
                _apply_pipeline_delta(
                    conn, int(task["nm_id"] or 0), str(task["supplier_article"] or ""),
                    -qty, inbound_delta, expected,
                )
                cur = conn.execute(
                    """
                    INSERT INTO inventory_movements(
                        event_key,movement_type,nm_id,supplier_article,product_name,quantity,
                        ready_delta,inbound_delta,route,movement_date,expected_arrival_date,
                        source_task_key,reference_movement_id,status,note,created_at,reversed_at,
                        goods_cost_rub,goods_unit_cost_rub
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        _next_event_key(conn, "dispatch", source_key), "dispatch",
                        int(task["nm_id"] or 0), str(task["supplier_article"] or ""),
                        str(task["product_name"] or ""), qty, -qty, inbound_delta, route,
                        dispatch_date, expected, source_key, None, status,
                        "Проведение фактической отгрузки",
                        datetime.now().isoformat(timespec="seconds"), None, 0.0, 0.0,
                    ),
                )
                movement_id = int(cur.lastrowid)
                destination = None if route == "FBS" else "inbound"
                goods_cost, allocations = _move_finished_goods_fifo(
                    conn, movement_id, int(task["nm_id"] or 0), str(task["supplier_article"] or ""),
                    str(task["product_name"] or ""), qty, "ready", destination,
                )
                note = ", ".join(f"слой #{int(a['layer_id'])}: {int(a['units'])} ед." for a in allocations)
                conn.execute(
                    "UPDATE inventory_movements SET goods_cost_rub=?,goods_unit_cost_rub=?,note=? WHERE id=?",
                    (goods_cost, round(goods_cost/qty, 4) if qty else 0.0,
                     ("FBS: себестоимость списана" if route == "FBS" else "Стоимость перемещена в поставку") + f"; {note}", movement_id),
                )
                conn.execute("RELEASE SAVEPOINT post_dispatch_task")
                result["posted"] += 1
                result["units"] += qty
                result["cost_rub"] += goods_cost
            except Exception as exc:
                conn.execute("ROLLBACK TO SAVEPOINT post_dispatch_task")
                conn.execute("RELEASE SAVEPOINT post_dispatch_task")
                result["errors"].append(f"{task['supplier_article']}: {exc}")
    result["cost_rub"] = round(float(result["cost_rub"]), 2)
    return result

def post_wb_receipts(task_keys: Iterable[str] | None = None) -> dict[str, Any]:
    from .core import connect
    from .fifo_finished_goods import _move_dispatch_allocation_to_wb
    from .production import _active_movement, _apply_pipeline_delta, _movement_date_text, _next_event_key, _recompute_inbound_date
    result: dict[str, Any] = {"posted": 0, "skipped": 0, "errors": [], "units": 0, "cost_rub": 0.0}
    keys = [str(k) for k in (task_keys or []) if str(k).strip()]
    with connect() as conn:
        sql = """
            SELECT * FROM execution_tasks
             WHERE task_type='dispatch' AND status IN ('Принято WB','Закрыто') AND actual_units>0
        """
        params: list[Any] = []
        if keys:
            sql += f" AND task_key IN ({','.join('?' for _ in keys)})"
            params.extend(keys)
        sql += " ORDER BY nm_id, task_key"
        tasks = conn.execute(sql, params).fetchall()
        for task in tasks:
            source_key = str(task["task_key"])
            route = str(task["route"] or "")
            if route == "FBS":
                result["skipped"] += 1
                continue
            if _active_movement(conn, "wb_receipt", source_key):
                result["skipped"] += 1
                continue
            dispatch = _active_movement(conn, "dispatch", source_key)
            if dispatch is None:
                result["errors"].append(f"{task['supplier_article']}: сначала проведите отгрузку.")
                continue
            if str(dispatch["status"] or "") != "open":
                result["skipped"] += 1
                continue
            qty = max(0, int(dispatch["quantity"] or task["actual_units"] or 0))
            conn.execute("SAVEPOINT post_wb_receipt_task")
            try:
                _apply_pipeline_delta(
                    conn, int(task["nm_id"] or 0), str(task["supplier_article"] or ""), 0, -qty
                )
                conn.execute("UPDATE inventory_movements SET status='closed' WHERE id=?", (int(dispatch["id"]),))
                cur = conn.execute(
                    """
                    INSERT INTO inventory_movements(
                        event_key,movement_type,nm_id,supplier_article,product_name,quantity,
                        ready_delta,inbound_delta,route,movement_date,expected_arrival_date,
                        source_task_key,reference_movement_id,status,note,created_at,reversed_at,
                        goods_cost_rub,goods_unit_cost_rub
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        _next_event_key(conn, "wb_receipt", source_key), "wb_receipt",
                        int(task["nm_id"] or 0), str(task["supplier_article"] or ""),
                        str(task["product_name"] or ""), qty, 0, -qty, route,
                        _movement_date_text(task["expected_arrival_date"]) or datetime.now().date().isoformat(),
                        _movement_date_text(task["expected_arrival_date"]), source_key, int(dispatch["id"]),
                        "applied", "Поставка принята WB", datetime.now().isoformat(timespec="seconds"), None,
                        0.0, 0.0,
                    ),
                )
                receipt_id = int(cur.lastrowid)
                goods_cost, allocations = _move_dispatch_allocation_to_wb(
                    conn, receipt_id, int(dispatch["id"]), qty
                )
                note = ", ".join(f"слой #{int(a['layer_id'])}: {int(a['units'])} ед." for a in allocations)
                conn.execute(
                    "UPDATE inventory_movements SET goods_cost_rub=?,goods_unit_cost_rub=?,note=? WHERE id=?",
                    (goods_cost, round(goods_cost/qty, 4) if qty else 0.0,
                     f"Поставка принята WB; стоимость перемещена из пути на WB; {note}", receipt_id),
                )
                _recompute_inbound_date(conn, int(task["nm_id"] or 0))
                conn.execute("RELEASE SAVEPOINT post_wb_receipt_task")
                result["posted"] += 1
                result["units"] += qty
                result["cost_rub"] += goods_cost
            except Exception as exc:
                conn.execute("ROLLBACK TO SAVEPOINT post_wb_receipt_task")
                conn.execute("RELEASE SAVEPOINT post_wb_receipt_task")
                result["errors"].append(f"{task['supplier_article']}: {exc}")
    result["cost_rub"] = round(float(result["cost_rub"]), 2)
    return result

def undo_inventory_movement(movement_id: int) -> dict[str, Any]:
    from .core import connect
    from .fifo_finished_goods import _reverse_finished_goods_allocations, _reverse_finished_goods_source_layer
    from .fifo_materials import _reverse_fifo_consumption, _reverse_procurement_material_layer
    from .procurement import _refresh_procurement_order_status
    from .production import _apply_material_delta, _apply_pipeline_delta, _recompute_inbound_date
    from .wip import _reverse_wip_allocations, _reverse_wip_completion, _reverse_wip_issue_batch
    result: dict[str, Any] = {"ok": False, "message": ""}
    with connect() as conn:
        movement = conn.execute("SELECT * FROM inventory_movements WHERE id=?", (int(movement_id),)).fetchone()
        if movement is None:
            result["message"] = "Операция не найдена."
            return result
        if movement["reversed_at"] is not None or str(movement["movement_type"]) == "reversal":
            result["message"] = "Эта операция уже отменена или является отменой."
            return result
        if str(movement["movement_type"]) == "dispatch" and str(movement["status"] or "") == "closed":
            result["message"] = "Сначала отмените приёмку WB, затем отгрузку."
            return result
        try:
            conn.execute("SAVEPOINT undo_inventory_event")
            material_delta = float(movement["material_delta"] or 0) if "material_delta" in movement.keys() else 0.0
            movement_type = str(movement["movement_type"] or "")
            if movement_type in {"dispatch", "wb_receipt", "wb_cost_reconciliation", "wb_incident_loss"}:
                _reverse_finished_goods_allocations(conn, int(movement["id"]))
            elif movement_type in {"production_receipt", "production_receipt_wip", "procurement_product_receipt"}:
                _reverse_finished_goods_source_layer(conn, int(movement["id"]))
            if movement_type == "production_receipt":
                _reverse_fifo_consumption(conn, int(movement["id"]))
            elif movement_type == "production_receipt_wip":
                _reverse_wip_allocations(conn, int(movement["id"]))
            elif movement_type == "wip_blank_receipt":
                _reverse_wip_completion(conn, int(movement["id"]))
            elif movement_type == "wip_material_issue":
                _reverse_wip_issue_batch(conn, int(movement["id"]))
                _reverse_fifo_consumption(conn, int(movement["id"]))
            elif movement_type == "procurement_material_receipt":
                _reverse_procurement_material_layer(conn, int(movement["id"]))
            if abs(material_delta) > 0.0005:
                _apply_material_delta(
                    conn, str(movement["material_key"] or ""), str(movement["material_name"] or ""), -material_delta
                )
            movement_nm_id = int(movement["nm_id"] or 0)
            ready_reverse = -int(movement["ready_delta"] or 0)
            inbound_reverse = -int(movement["inbound_delta"] or 0)
            if movement_nm_id > 0 or ready_reverse != 0 or inbound_reverse != 0:
                _apply_pipeline_delta(
                    conn, movement_nm_id, str(movement["supplier_article"] or ""),
                    ready_reverse, inbound_reverse,
                )
            procurement_item_id = int(movement["procurement_item_id"] or 0) if "procurement_item_id" in movement.keys() else 0
            procurement_order_id = int(movement["procurement_order_id"] or 0) if "procurement_order_id" in movement.keys() else 0
            procurement_quantity = float(movement["procurement_quantity"] or 0) if "procurement_quantity" in movement.keys() else 0.0
            if procurement_item_id > 0 and procurement_quantity > 0:
                conn.execute(
                    """
                    UPDATE procurement_items
                       SET posted_quantity=MAX(0,posted_quantity-?),
                           received_quantity=MAX(0,received_quantity-?),updated_at=?
                     WHERE id=?
                    """,
                    (procurement_quantity, procurement_quantity, datetime.now().isoformat(timespec="seconds"), procurement_item_id),
                )
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "UPDATE inventory_movements SET status='reversed', reversed_at=? WHERE id=?",
                (now, int(movement["id"])),
            )
            if movement_type == "wb_incident_loss":
                conn.execute(
                    "UPDATE wb_incident_loss_lines SET status='reversed',reversed_at=? WHERE movement_id=? AND status='applied'",
                    (now,int(movement["id"])),
                )
            if str(movement["movement_type"]) == "wb_receipt" and movement["reference_movement_id"]:
                conn.execute(
                    "UPDATE inventory_movements SET status='open' WHERE id=? AND reversed_at IS NULL",
                    (int(movement["reference_movement_id"]),),
                )
            conn.execute(
                """
                INSERT INTO inventory_movements(
                    event_key,movement_type,nm_id,supplier_article,product_name,quantity,
                    ready_delta,inbound_delta,route,movement_date,expected_arrival_date,
                    source_task_key,reference_movement_id,status,note,created_at,reversed_at,
                    material_key,material_name,material_delta,
                    procurement_order_id,procurement_item_id,procurement_quantity,
                    material_cost_rub,unit_cost_rub,goods_cost_rub,goods_unit_cost_rub
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"reversal|{int(movement['id'])}", "reversal", int(movement["nm_id"] or 0),
                    str(movement["supplier_article"] or ""), str(movement["product_name"] or ""),
                    int(movement["quantity"] or 0), -int(movement["ready_delta"] or 0),
                    -int(movement["inbound_delta"] or 0), str(movement["route"] or ""),
                    datetime.now().date().isoformat(), movement["expected_arrival_date"],
                    str(movement["source_task_key"] or ""), int(movement["id"]), "applied",
                    f"Отмена операции #{int(movement['id'])}", now, None,
                    str(movement["material_key"] or "") if "material_key" in movement.keys() else "",
                    str(movement["material_name"] or "") if "material_name" in movement.keys() else "",
                    -material_delta, procurement_order_id or None, procurement_item_id or None,
                    -procurement_quantity,
                    -float(movement["material_cost_rub"] or 0) if "material_cost_rub" in movement.keys() else 0.0,
                    -float(movement["unit_cost_rub"] or 0) if "unit_cost_rub" in movement.keys() else 0.0,
                    -float(movement["goods_cost_rub"] or 0) if "goods_cost_rub" in movement.keys() else 0.0,
                    -float(movement["goods_unit_cost_rub"] or 0) if "goods_unit_cost_rub" in movement.keys() else 0.0,
                ),
            )
            if procurement_order_id > 0:
                _refresh_procurement_order_status(conn, procurement_order_id)
            if movement_nm_id > 0:
                _recompute_inbound_date(conn, movement_nm_id)
            conn.execute("RELEASE SAVEPOINT undo_inventory_event")
            result["ok"] = True
            result["message"] = "Операция отменена, остатки восстановлены."
        except Exception as exc:
            try:
                conn.execute("ROLLBACK TO SAVEPOINT undo_inventory_event")
                conn.execute("RELEASE SAVEPOINT undo_inventory_event")
            except Exception:
                pass
            result["message"] = str(exc)
    return result

def read_inventory_movements(limit: int = 300) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            "SELECT * FROM inventory_movements ORDER BY id DESC LIMIT ?",
            conn,
            params=(max(1, int(limit)),),
        )
