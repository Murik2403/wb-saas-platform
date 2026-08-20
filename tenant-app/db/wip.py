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


def _wip_module_enabled(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM app_meta WHERE key='wip_module_enabled'").fetchone()
    return bool(row and str(row["value"] or "0").strip() == "1")

def wip_module_status() -> dict[str, Any]:
    from .core import connect
    with connect() as conn:
        enabled = _wip_module_enabled(conn)
        open_row = conn.execute(
            "SELECT COUNT(*) AS batches,COALESCE(SUM(issued_meters),0) AS meters,COALESCE(SUM(material_cost_rub),0) AS cost FROM wip_blank_batches WHERE status='open' AND reversed_at IS NULL"
        ).fetchone()
        stock_row = conn.execute(
            "SELECT COALESCE(SUM(remaining_units),0) AS units,COALESCE(SUM(remaining_units*unit_cost_rub),0) AS cost FROM wip_blank_batches WHERE status IN ('active','depleted') AND reversed_at IS NULL"
        ).fetchone()
        return {
            "enabled": enabled,
            "open_batches": int(open_row["batches"] or 0) if open_row else 0,
            "open_meters": float(open_row["meters"] or 0) if open_row else 0.0,
            "open_cost_rub": float(open_row["cost"] or 0) if open_row else 0.0,
            "remaining_units": int(stock_row["units"] or 0) if stock_row else 0,
            "remaining_cost_rub": float(stock_row["cost"] or 0) if stock_row else 0.0,
        }

def read_wip_blank_batches(active_only: bool = False) -> pd.DataFrame:
    from .core import connect
    where = "WHERE reversed_at IS NULL" if active_only else ""
    with connect() as conn:
        return pd.read_sql_query(
            f"""
            SELECT b.*,
                   MAX(0,b.produced_units-b.remaining_units) AS consumed_units,
                   ROUND(b.remaining_units*b.unit_cost_rub,2) AS remaining_cost_rub
              FROM wip_blank_batches b
              {where}
             ORDER BY COALESCE(batch_date,'1900-01-01') DESC,id DESC
            """,
            conn,
        )

def read_wip_blank_summary() -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            SELECT material_key,material_name,blank_type,
                   SUM(CASE WHEN status='open' AND reversed_at IS NULL THEN issued_meters ELSE 0 END) AS open_meters,
                   SUM(CASE WHEN status='open' AND reversed_at IS NULL THEN material_cost_rub ELSE 0 END) AS open_cost_rub,
                   SUM(CASE WHEN status IN ('active','depleted') AND reversed_at IS NULL THEN remaining_units ELSE 0 END) AS remaining_units,
                   SUM(CASE WHEN status IN ('active','depleted') AND reversed_at IS NULL THEN remaining_units*unit_cost_rub ELSE 0 END) AS remaining_cost_rub,
                   CASE WHEN SUM(CASE WHEN status IN ('active','depleted') AND reversed_at IS NULL THEN remaining_units ELSE 0 END)>0
                        THEN SUM(CASE WHEN status IN ('active','depleted') AND reversed_at IS NULL THEN remaining_units*unit_cost_rub ELSE 0 END)
                             / SUM(CASE WHEN status IN ('active','depleted') AND reversed_at IS NULL THEN remaining_units ELSE 0 END)
                        ELSE 0 END AS avg_unit_cost_rub,
                   COUNT(CASE WHEN status='open' AND reversed_at IS NULL THEN 1 END) AS open_batches,
                   COUNT(CASE WHEN status IN ('active','depleted') AND reversed_at IS NULL THEN 1 END) AS completed_batches
              FROM wip_blank_batches
             WHERE reversed_at IS NULL
             GROUP BY material_key,material_name,blank_type
             ORDER BY material_name,blank_type
            """,
            conn,
        )

def read_wip_blank_allocations(limit: int = 500) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            SELECT a.*,b.batch_key,b.batch_date,b.material_name,b.blank_type,b.unit_cost_rub,
                   m.nm_id,m.supplier_article,m.product_name,m.movement_date,m.note AS movement_note
              FROM wip_blank_allocations a
              JOIN wip_blank_batches b ON b.id=a.batch_id
              JOIN inventory_movements m ON m.id=a.movement_id
             ORDER BY a.id DESC LIMIT ?
            """,
            conn,
            params=(max(1, int(limit)),),
        )

def issue_wip_material(
    batch_date: Any,
    material_name: str,
    blank_type: str,
    meters_used: float,
    note: str = "",
    source_key: str | None = None,
) -> dict[str, Any]:
    from .core import connect
    from .fifo_materials import _consume_material_fifo
    from .production import _apply_material_delta, _material_key, _movement_date_text
    date_value = _movement_date_text(batch_date) or datetime.now().date().isoformat()
    material_name = str(material_name or "").strip()
    blank_type = str(blank_type or "").strip()
    meters = round(max(0.0, float(meters_used or 0)), 3)
    result: dict[str, Any] = {"posted": 0, "skipped": 0, "errors": [], "meters": 0.0, "cost_rub": 0.0, "batch_id": 0}
    if not material_name:
        result["errors"].append("Не указан материал/цвет.")
        return result
    if not blank_type:
        result["errors"].append("Не указан тип заготовки.")
        return result
    if meters <= 0.0005:
        result["errors"].append("Расход сырья должен быть больше нуля.")
        return result
    key = _material_key(material_name)
    batch_key = str(source_key or "").strip() or f"wip:{date_value}:{key}:{blank_type.casefold()}:{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    with connect() as conn:
        existing = conn.execute("SELECT id FROM wip_blank_batches WHERE batch_key=?", (batch_key,)).fetchone()
        if existing:
            result["skipped"] = 1
            result["batch_id"] = int(existing["id"])
            return result
        conn.execute("SAVEPOINT issue_wip_material")
        try:
            _apply_material_delta(conn, key, material_name, -meters)
            event_key = f"wip_material_issue|{batch_key}|v1"
            movement_cur = conn.execute(
                """
                INSERT INTO inventory_movements(
                    event_key,movement_type,nm_id,supplier_article,product_name,quantity,
                    ready_delta,inbound_delta,route,movement_date,expected_arrival_date,
                    source_task_key,reference_movement_id,status,note,created_at,reversed_at,
                    material_key,material_name,material_delta,material_cost_rub,unit_cost_rub,
                    goods_cost_rub,goods_unit_cost_rub
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_key,"wip_material_issue",0,"",f"НЗП: {blank_type}",0,0,0,"",date_value,None,
                    batch_key,None,"applied",f"Выдано в НЗП {meters:.3f} м материала «{material_name}»; {note}",
                    datetime.now().isoformat(timespec="seconds"),None,key,material_name,-meters,0.0,0.0,0.0,0.0,
                ),
            )
            movement_id = int(movement_cur.lastrowid)
            material_cost, allocations = _consume_material_fifo(conn, movement_id, key, material_name, meters)
            allocation_note = ", ".join(f"слой #{int(a['layer_id'])}: {a['meters']:.3f} м" for a in allocations)
            conn.execute(
                "UPDATE inventory_movements SET material_cost_rub=?,note=? WHERE id=?",
                (material_cost, f"Выдано в НЗП {meters:.3f} м «{material_name}» для «{blank_type}»; FIFO {material_cost:.2f} ₽; {allocation_note}; {note}", movement_id),
            )
            now = datetime.now().isoformat(timespec="seconds")
            cur = conn.execute(
                """
                INSERT INTO wip_blank_batches(
                    batch_key,issue_movement_id,completion_movement_id,batch_date,material_key,material_name,
                    blank_type,issued_meters,material_cost_rub,produced_units,scrap_units,remaining_units,
                    unit_cost_rub,status,note,created_at,updated_at,reversed_at
                ) VALUES (?,?,NULL,?,?,?,?,?,?,0,0,0,0,'open',?,?,?,NULL)
                """,
                (batch_key,movement_id,date_value,key,material_name,blank_type,meters,material_cost,str(note or ""),now,now),
            )
            batch_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO app_meta(key,value,updated_at) VALUES ('wip_module_enabled','1',?)
                ON CONFLICT(key) DO UPDATE SET value='1',updated_at=excluded.updated_at
                """,
                (now,),
            )
            conn.execute("RELEASE SAVEPOINT issue_wip_material")
            result.update({"posted": 1, "meters": meters, "cost_rub": material_cost, "batch_id": batch_id})
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT issue_wip_material")
            conn.execute("RELEASE SAVEPOINT issue_wip_material")
            result["errors"].append(str(exc))
    return result

def complete_wip_blank_batch(
    batch_id: int,
    produced_units: int,
    scrap_units: int = 0,
    note: str = "",
) -> dict[str, Any]:
    from .core import connect
    qty = max(0, int(produced_units or 0))
    scrap = max(0, int(scrap_units or 0))
    result: dict[str, Any] = {"posted": 0, "skipped": 0, "errors": [], "units": 0, "cost_rub": 0.0, "batch_id": int(batch_id or 0)}
    if qty <= 0:
        result["errors"].append("Количество годных заготовок должно быть больше нуля.")
        return result
    with connect() as conn:
        batch = conn.execute("SELECT * FROM wip_blank_batches WHERE id=?", (int(batch_id),)).fetchone()
        if batch is None:
            result["errors"].append("Партия НЗП не найдена.")
            return result
        if batch["reversed_at"] is not None or str(batch["status"] or "") == "reversed":
            result["errors"].append("Партия НЗП отменена.")
            return result
        if int(batch["completion_movement_id"] or 0) > 0 or str(batch["status"] or "") != "open":
            result["skipped"] = 1
            result["units"] = int(batch["produced_units"] or 0)
            return result
        conn.execute("SAVEPOINT complete_wip_batch")
        try:
            unit_cost = round(float(batch["material_cost_rub"] or 0) / qty, 6)
            source_key = f"wip_batch:{int(batch_id)}:completion"
            event_key = f"wip_blank_receipt|{source_key}|v1"
            now = datetime.now().isoformat(timespec="seconds")
            cur = conn.execute(
                """
                INSERT INTO inventory_movements(
                    event_key,movement_type,nm_id,supplier_article,product_name,quantity,
                    ready_delta,inbound_delta,route,movement_date,expected_arrival_date,
                    source_task_key,reference_movement_id,status,note,created_at,reversed_at,
                    material_key,material_name,material_delta,material_cost_rub,unit_cost_rub,
                    goods_cost_rub,goods_unit_cost_rub
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_key,"wip_blank_receipt",0,"",f"НЗП: {batch['blank_type']}",qty,0,0,"",
                    str(batch["batch_date"] or datetime.now().date().isoformat()),None,source_key,
                    int(batch["issue_movement_id"] or 0) or None,"applied",
                    f"Закрыта партия НЗП #{int(batch_id)}: годных {qty}, брак {scrap}; {note}",now,None,
                    str(batch["material_key"] or ""),str(batch["material_name"] or ""),0.0,
                    float(batch["material_cost_rub"] or 0),unit_cost,float(batch["material_cost_rub"] or 0),unit_cost,
                ),
            )
            movement_id = int(cur.lastrowid)
            merged_note = "; ".join(x for x in [str(batch["note"] or "").strip(), str(note or "").strip()] if x)
            conn.execute(
                """
                UPDATE wip_blank_batches
                   SET completion_movement_id=?,produced_units=?,scrap_units=?,remaining_units=?,
                       unit_cost_rub=?,status='active',note=?,updated_at=?
                 WHERE id=?
                """,
                (movement_id,qty,scrap,qty,unit_cost,merged_note,now,int(batch_id)),
            )
            conn.execute("RELEASE SAVEPOINT complete_wip_batch")
            result.update({"posted": 1, "units": qty, "cost_rub": float(batch["material_cost_rub"] or 0)})
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT complete_wip_batch")
            conn.execute("RELEASE SAVEPOINT complete_wip_batch")
            result["errors"].append(str(exc))
    return result

def post_wip_blank_batch(
    batch_date: Any,
    material_name: str,
    blank_type: str,
    meters_used: float,
    produced_units: int,
    scrap_units: int = 0,
    note: str = "",
    source_key: str | None = None,
) -> dict[str, Any]:
    issued = issue_wip_material(batch_date, material_name, blank_type, meters_used, note, source_key)
    result = dict(issued)
    result.setdefault("units", 0)
    if issued.get("errors") or not int(issued.get("batch_id", 0) or 0):
        return result
    completed = complete_wip_blank_batch(int(issued["batch_id"]), produced_units, scrap_units, note)
    result["units"] = int(completed.get("units", 0) or 0)
    result["posted"] = int(issued.get("posted", 0) or 0) + int(completed.get("posted", 0) or 0)
    result["skipped"] = int(issued.get("skipped", 0) or 0) + int(completed.get("skipped", 0) or 0)
    result["errors"] = list(issued.get("errors", []) or []) + list(completed.get("errors", []) or [])
    return result

def _wip_available_units(conn: sqlite3.Connection, material_key: str, blank_type: str) -> int:
    from .production import _material_key
    row = conn.execute(
        """
        SELECT COALESCE(SUM(remaining_units),0) AS units
          FROM wip_blank_batches
         WHERE material_key=? AND blank_type=? AND status='active' AND reversed_at IS NULL AND remaining_units>0
        """,
        (_material_key(material_key), str(blank_type or "").strip()),
    ).fetchone()
    return max(0, int(row["units"] or 0) if row else 0)

def _consume_wip_fifo(
    conn: sqlite3.Connection,
    movement_id: int,
    material_key: str,
    material_name: str,
    blank_type: str,
    units: int,
) -> tuple[float, list[dict[str, Any]]]:
    from .production import _material_key
    required = max(0, int(units or 0))
    if required <= 0:
        return 0.0, []
    key = _material_key(material_key or material_name)
    blank = str(blank_type or "").strip()
    available = _wip_available_units(conn, key, blank)
    if available < required:
        raise ValueError(
            f"Недостаточно заготовок «{material_name} / {blank}»: доступно {available} шт., требуется {required} шт."
        )
    rows = conn.execute(
        """
        SELECT * FROM wip_blank_batches
         WHERE material_key=? AND blank_type=? AND status='active' AND reversed_at IS NULL AND remaining_units>0
         ORDER BY COALESCE(batch_date,'1900-01-01'),id
        """,
        (key, blank),
    ).fetchall()
    remaining = required
    total = 0.0
    allocations: list[dict[str, Any]] = []
    now = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        if remaining <= 0:
            break
        current = max(0, int(row["remaining_units"] or 0))
        take = min(current, remaining)
        if take <= 0:
            continue
        amount = round(take * float(row["unit_cost_rub"] or 0), 2)
        new_remaining = current - take
        conn.execute(
            "UPDATE wip_blank_batches SET remaining_units=?,status=?,updated_at=? WHERE id=?",
            (new_remaining,"depleted" if new_remaining <= 0 else "active",now,int(row["id"])),
        )
        conn.execute(
            """
            INSERT INTO wip_blank_allocations(movement_id,batch_id,units,amount_rub,status,created_at)
            VALUES (?,?,?,?,'applied',?)
            """,
            (int(movement_id),int(row["id"]),take,amount,now),
        )
        allocations.append({"batch_id": int(row["id"]), "units": take, "amount_rub": amount})
        total += amount
        remaining -= take
    if remaining > 0:
        raise ValueError(f"Не удалось списать {remaining} заготовок по FIFO.")
    return round(total, 2), allocations

def _reverse_wip_allocations(conn: sqlite3.Connection, movement_id: int) -> None:
    rows = conn.execute(
        """
        SELECT * FROM wip_blank_allocations
         WHERE movement_id=? AND status='applied' AND reversed_at IS NULL
         ORDER BY id DESC
        """,
        (int(movement_id),),
    ).fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        conn.execute(
            "UPDATE wip_blank_batches SET remaining_units=remaining_units+?,status='active',updated_at=? WHERE id=? AND reversed_at IS NULL",
            (int(row["units"] or 0),now,int(row["batch_id"])),
        )
        conn.execute(
            "UPDATE wip_blank_allocations SET status='reversed',reversed_at=? WHERE id=?",
            (now,int(row["id"])),
        )
    conn.execute(
        "UPDATE production_cost_batches SET status='reversed',reversed_at=? WHERE movement_id=? AND reversed_at IS NULL",
        (now, int(movement_id)),
    )

def _reverse_wip_issue_batch(conn: sqlite3.Connection, movement_id: int) -> None:
    batch = conn.execute("SELECT * FROM wip_blank_batches WHERE issue_movement_id=?", (int(movement_id),)).fetchone()
    if batch is None:
        return
    if int(batch["completion_movement_id"] or 0) > 0 or str(batch["status"] or "") != "open":
        raise ValueError("Сначала отмените выпуск заготовок или все последующие комплектации из этой партии.")
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE wip_blank_batches SET status='reversed',reversed_at=?,updated_at=? WHERE id=?",
        (now,now,int(batch["id"])),
    )

def _reverse_wip_completion(conn: sqlite3.Connection, movement_id: int) -> None:
    batch = conn.execute("SELECT * FROM wip_blank_batches WHERE completion_movement_id=?", (int(movement_id),)).fetchone()
    if batch is None:
        return
    allocated = conn.execute(
        "SELECT COALESCE(SUM(units),0) AS units FROM wip_blank_allocations WHERE batch_id=? AND status='applied' AND reversed_at IS NULL",
        (int(batch["id"]),),
    ).fetchone()
    if int(allocated["units"] or 0) > 0 or int(batch["remaining_units"] or 0) != int(batch["produced_units"] or 0):
        raise ValueError("Сначала отмените комплектацию готовых комплектов, использовавшую эту партию заготовок.")
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE wip_blank_batches
           SET completion_movement_id=NULL,produced_units=0,scrap_units=0,remaining_units=0,
               unit_cost_rub=0,status='open',updated_at=?
         WHERE id=?
        """,
        (now,int(batch["id"])),
    )

def _post_wip_packaging_conn(
    conn: sqlite3.Connection,
    nm_id: int,
    supplier_article: str,
    product_name: str,
    qty_sets: int,
    date_value: str,
    source_key: str,
    note: str = "",
) -> dict[str, Any]:
    from .fifo_finished_goods import _create_finished_goods_layer
    from .production import _active_movement, _apply_pipeline_delta, _material_key, _next_event_key
    qty = max(0, int(qty_sets or 0))
    if qty <= 0:
        raise ValueError("Количество комплектов должно быть больше нуля.")
    cfg = conn.execute("SELECT * FROM production_settings WHERE nm_id=? AND enabled=1", (int(nm_id),)).fetchone()
    if cfg is None:
        raise ValueError("Товар не включён в собственное производство.")
    material_name = str(cfg["material_name"] or "").strip()
    material_key = _material_key(material_name)
    blank_type = str(cfg["blank_type"] or "").strip()
    pack_size = max(1, int(cfg["pack_size"] or 1))
    if not material_name or not blank_type:
        raise ValueError("Для товара не заполнены материал/цвет или тип заготовки.")
    required_blanks = qty * pack_size
    if _active_movement(conn, "production_receipt_wip", source_key) or _active_movement(conn, "production_receipt", source_key):
        return {"posted": 0, "skipped": 1, "units": 0, "wip_units": 0, "cost_rub": 0.0}
    _apply_pipeline_delta(conn, int(nm_id), str(supplier_article or ""), qty, 0)
    event_key = _next_event_key(conn, "production_receipt_wip", source_key)
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO inventory_movements(
            event_key,movement_type,nm_id,supplier_article,product_name,quantity,
            ready_delta,inbound_delta,route,movement_date,expected_arrival_date,
            source_task_key,reference_movement_id,status,note,created_at,reversed_at,
            material_key,material_name,material_delta,material_cost_rub,unit_cost_rub,
            goods_cost_rub,goods_unit_cost_rub
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            event_key,"production_receipt_wip",int(nm_id),str(supplier_article or ""),str(product_name or ""),
            qty,qty,0,"",date_value,None,source_key,None,"applied",
            f"Комплектация из НЗП: {required_blanks} заготовок «{material_name} / {blank_type}»; {note}",
            now,None,material_key,material_name,0.0,0.0,0.0,0.0,0.0,
        ),
    )
    movement_id = int(cur.lastrowid)
    wip_cost, allocations = _consume_wip_fifo(
        conn,movement_id,material_key,material_name,blank_type,required_blanks
    )
    cost_row = conn.execute("SELECT * FROM costs WHERE nm_id=?", (int(nm_id),)).fetchone()
    packaging_unit = max(0.0, float(cost_row["packaging_cost_rub"] or 0)) if cost_row else 0.0
    if packaging_unit <= 0:
        packaging_unit = 12.0
    labor_unit = max(0.0, float(cost_row["labor_cost_rub"] or 0)) if cost_row else 0.0
    other_unit = max(0.0, float(cost_row["other_cost_rub"] or 0)) if cost_row else 0.0
    packaging_total = round(packaging_unit * qty, 2)
    labor_total = round(labor_unit * qty, 2)
    other_total = round(other_unit * qty, 2)
    total_cost = round(wip_cost + packaging_total + labor_total + other_total, 2)
    unit_cost = round(total_cost / qty, 4) if qty else 0.0
    allocation_note = ", ".join(f"НЗП #{int(a['batch_id'])}: {int(a['units'])} шт" for a in allocations)
    conn.execute(
        """
        UPDATE inventory_movements
           SET material_cost_rub=?,unit_cost_rub=?,goods_cost_rub=?,goods_unit_cost_rub=?,note=?
         WHERE id=?
        """,
        (wip_cost,unit_cost,total_cost,unit_cost,
         f"Комплектация из НЗП: {required_blanks} заготовок, FIFO {wip_cost:.2f} ₽; партия {unit_cost:.2f} ₽/компл.; {allocation_note}; {note}",
         movement_id),
    )
    conn.execute(
        """
        INSERT INTO production_cost_batches(
            movement_id,nm_id,supplier_article,product_name,batch_date,produced_units,
            material_key,material_name,material_meters,material_cost_rub,
            packaging_cost_rub,labor_cost_rub,other_cost_rub,total_cost_rub,unit_cost_rub,
            status,note,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?)
        """,
        (movement_id,int(nm_id),str(supplier_article or ""),str(product_name or ""),date_value,qty,
         material_key,material_name,0.0,wip_cost,packaging_total,labor_total,other_total,total_cost,unit_cost,
         f"Комплектация из НЗП; {required_blanks} заготовок; {allocation_note}",now),
    )
    _create_finished_goods_layer(
        conn,int(nm_id),str(supplier_article or ""),str(product_name or ""),
        "production_wip",f"movement:{movement_id}",date_value,qty,unit_cost,"ready",
        f"Оприходовано из НЗП; {required_blanks} заготовок списано по FIFO.",
    )
    return {"posted": 1, "skipped": 0, "units": qty, "wip_units": required_blanks, "cost_rub": total_cost, "movement_id": movement_id}

def post_manual_wip_packaging(
    nm_id: int,
    qty_sets: int,
    packaging_date: Any = None,
    note: str = "",
    source_key: str | None = None,
) -> dict[str, Any]:
    from .core import connect
    from .production import _movement_date_text
    date_value = _movement_date_text(packaging_date) or datetime.now().date().isoformat()
    result: dict[str, Any] = {"posted": 0, "skipped": 0, "errors": [], "units": 0, "wip_units": 0, "cost_rub": 0.0}
    with connect() as conn:
        cfg = conn.execute(
            """
            SELECT p.*,COALESCE(c.product_name,'') AS product_name_catalog
              FROM production_settings p LEFT JOIN products_catalog c ON c.nm_id=p.nm_id
             WHERE p.nm_id=? AND p.enabled=1
            """,
            (int(nm_id),),
        ).fetchone()
        if cfg is None:
            result["errors"].append("Товар не найден среди производимых.")
            return result
        key = str(source_key or "").strip() or f"manual_wip_packaging:{int(nm_id)}:{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        conn.execute("SAVEPOINT manual_wip_packaging")
        try:
            posted = _post_wip_packaging_conn(
                conn,int(nm_id),str(cfg["supplier_article"] or ""),str(cfg["product_name_catalog"] or ""),
                int(qty_sets),date_value,key,str(note or ""),
            )
            conn.execute("RELEASE SAVEPOINT manual_wip_packaging")
            result.update(posted)
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT manual_wip_packaging")
            conn.execute("RELEASE SAVEPOINT manual_wip_packaging")
            result["errors"].append(str(exc))
    return result
