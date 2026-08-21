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


def save_production_settings(df: pd.DataFrame) -> None:
    from .core import connect
    required = {"nm_id", "enabled", "material_per_unit", "target_days", "min_batch"}
    if not required.issubset(df.columns):
        raise ValueError("Не хватает колонок настроек производства")
    rows = []
    for _, r in df.iterrows():
        if pd.isna(r.get("nm_id")):
            continue
        rows.append(
            (
                int(r["nm_id"]),
                str(r.get("supplier_article", "") or ""),
                1 if bool(r.get("enabled", False)) else 0,
                max(0.0, float(r.get("material_per_unit", 0) or 0)),
                max(1, int(float(r.get("target_days", 21) or 21))),
                max(1, int(float(r.get("min_batch", 1) or 1))),
                str(r.get("note", "") or ""),
                str(r.get("blank_type", "") or ""),
                max(1, int(float(r.get("pack_size", 4) or 4))),
                1 if bool(r.get("auto_rules", False)) else 0,
                str(r.get("material_name", "") or "").strip(),
            )
        )
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO production_settings(
                nm_id,supplier_article,enabled,material_per_unit,target_days,min_batch,note,
                blank_type,pack_size,auto_rules,material_name
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(nm_id) DO UPDATE SET
                supplier_article=excluded.supplier_article,
                enabled=excluded.enabled,
                material_per_unit=excluded.material_per_unit,
                target_days=excluded.target_days,
                min_batch=excluded.min_batch,
                note=excluded.note,
                blank_type=excluded.blank_type,
                pack_size=excluded.pack_size,
                auto_rules=excluded.auto_rules,
                material_name=excluded.material_name
            """,
            rows,
        )

# Sentinel roll_length for materials tracked as a plain running quantity (no
# fixed-size packaging) -- see ui_helpers.py's NO_PACKAGE_ROLL_LENGTH for the
# full rationale. Duplicated as a plain constant here (rather than imported)
# because ui_helpers.py imports from this db package, not the other way round.
NO_PACKAGE_ROLL_LENGTH = 1_000_000_000.0


def save_material_inventory(df: pd.DataFrame) -> None:
    from .core import connect
    required = {"material_name", "balance_known", "full_rolls", "partial_meters", "roll_length"}
    if not required.issubset(df.columns):
        raise ValueError("Не хватает колонок учёта сырья")
    has_unit = "unit" in df.columns
    has_mode = "tracking_mode" in df.columns
    has_rate = "opening_rate_rub" in df.columns
    rows = []
    for _, r in df.iterrows():
        material_name = str(r.get("material_name", "") or "").strip()
        if not material_name:
            continue
        key = material_name.casefold()
        tracking_mode = str(r.get("tracking_mode", "packaged") or "packaged").strip() if has_mode else "packaged"
        if tracking_mode not in {"packaged", "quantity"}:
            tracking_mode = "packaged"
        if tracking_mode == "quantity":
            # No fixed package size for this material -- the whole stock is one
            # running number. Pin full_rolls at 0 and roll_length at the
            # sentinel so downstream code (_apply_material_delta, FIFO opening
            # totals, etc.) keeps treating partial_meters as the entire
            # quantity, exactly like it already does for full_rolls=0 today.
            full_rolls = 0
            roll_length = NO_PACKAGE_ROLL_LENGTH
        else:
            full_rolls = max(0, int(float(r.get("full_rolls", 0) or 0)))
            roll_length = max(0.1, float(r.get("roll_length", 25.5) or 25.5))
        unit = (str(r.get("unit", "") or "").strip() or "м") if has_unit else "м"
        opening_rate_rub = max(0.0, float(r.get("opening_rate_rub", 0) or 0)) if has_rate else 0.0
        rows.append((
            key, material_name,
            1 if bool(r.get("balance_known", False)) else 0,
            full_rolls,
            max(0.0, float(r.get("partial_meters", 0) or 0)),
            roll_length,
            unit, tracking_mode, opening_rate_rub,
            str(r.get("note", "") or ""),
            datetime.now().isoformat(timespec="seconds"),
        ))
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO material_inventory_color(
                material_key,material_name,balance_known,full_rolls,partial_meters,roll_length,
                unit,tracking_mode,opening_rate_rub,note,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(material_key) DO UPDATE SET
                material_name=excluded.material_name,
                balance_known=excluded.balance_known,
                full_rolls=excluded.full_rolls,
                partial_meters=excluded.partial_meters,
                roll_length=excluded.roll_length,
                unit=excluded.unit,
                tracking_mode=excluded.tracking_mode,
                opening_rate_rub=excluded.opening_rate_rub,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            rows,
        )

def save_product_pipeline(df: pd.DataFrame) -> None:
    from .core import connect
    required = {"nm_id", "local_known", "ready_units", "inbound_known", "inbound_units"}
    if not required.issubset(df.columns):
        raise ValueError("Не хватает колонок готовой продукции и поставок")
    rows = []
    for _, r in df.iterrows():
        if pd.isna(r.get("nm_id")):
            continue
        inbound_date = r.get("inbound_date")
        if pd.isna(inbound_date) or str(inbound_date).strip() in {"", "NaT", "None"}:
            inbound_date_text = None
        else:
            try:
                inbound_date_text = pd.to_datetime(inbound_date).date().isoformat()
            except Exception:
                inbound_date_text = None
        rows.append((
            int(r["nm_id"]),
            str(r.get("supplier_article", "") or ""),
            1 if bool(r.get("local_known", False)) else 0,
            max(0, int(float(r.get("ready_units", 0) or 0))),
            1 if bool(r.get("inbound_known", False)) else 0,
            max(0, int(float(r.get("inbound_units", 0) or 0))),
            inbound_date_text,
            str(r.get("note", "") or ""),
            datetime.now().isoformat(timespec="seconds"),
        ))
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO product_pipeline(
                nm_id,supplier_article,local_known,ready_units,inbound_known,inbound_units,inbound_date,note,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(nm_id) DO UPDATE SET
                supplier_article=excluded.supplier_article,
                local_known=excluded.local_known,
                ready_units=excluded.ready_units,
                inbound_known=excluded.inbound_known,
                inbound_units=excluded.inbound_units,
                inbound_date=excluded.inbound_date,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            rows,
        )

def save_production_capacity(
    capacity_known: bool, pieces_per_day: int, workdays: list[int], horizon_days: int,
    fulfillment_lead_days: int = 0, emergency_cover_days: int = 7,
    expedited_fbo_lead_days: int = 3, fbs_lead_days: int = 0, note: str = ""
) -> None:
    from .core import connect
    workday_text = ",".join(str(int(d)) for d in sorted(set(workdays)) if 0 <= int(d) <= 6)
    if not workday_text:
        workday_text = "0,1,2,3,4,5"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO production_capacity(
                id,capacity_known,pieces_per_day,workdays,horizon_days,
                fulfillment_lead_days,emergency_cover_days,expedited_fbo_lead_days,
                fbs_lead_days,note,updated_at
            )
            VALUES (1,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                capacity_known=excluded.capacity_known,
                pieces_per_day=excluded.pieces_per_day,
                workdays=excluded.workdays,
                horizon_days=excluded.horizon_days,
                fulfillment_lead_days=excluded.fulfillment_lead_days,
                emergency_cover_days=excluded.emergency_cover_days,
                expedited_fbo_lead_days=excluded.expedited_fbo_lead_days,
                fbs_lead_days=excluded.fbs_lead_days,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            (1 if capacity_known else 0, max(0, int(pieces_per_day)), workday_text,
             max(1, int(horizon_days)), max(0, int(fulfillment_lead_days)),
             max(1, int(emergency_cover_days)), max(0, int(expedited_fbo_lead_days)),
             max(0, int(fbs_lead_days)), str(note or ""),
             datetime.now().isoformat(timespec="seconds")),
        )

def get_production_capacity() -> dict[str, Any]:
    from .core import connect
    with connect() as conn:
        row = conn.execute("SELECT * FROM production_capacity WHERE id=1").fetchone()
        return dict(row) if row else {
            "id": 1, "capacity_known": 0, "pieces_per_day": 0,
            "workdays": "0,1,2,3,4,5", "horizon_days": 14,
            "fulfillment_lead_days": 0, "emergency_cover_days": 7,
            "expedited_fbo_lead_days": 3, "fbs_lead_days": 0, "note": ""
        }

def save_execution_tasks(df: pd.DataFrame) -> None:
    from .core import _int, connect
    required = {"task_key", "task_type", "planned_units"}
    if not required.issubset(df.columns):
        raise ValueError("Не хватает колонок исполнения плана")

    def _date_text(value: Any) -> str | None:
        if pd.isna(value) or str(value).strip() in {"", "NaT", "None"}:
            return None
        try:
            return pd.to_datetime(value).date().isoformat()
        except Exception:
            return None

    rows = []
    for _, r in df.iterrows():
        task_key = str(r.get("task_key", "") or "").strip()
        task_type = str(r.get("task_type", "") or "").strip()
        if not task_key or not task_type:
            continue
        rows.append((
            task_key,
            task_type,
            _date_text(r.get("task_date")),
            str(r.get("stage", "") or ""),
            _int(r.get("nm_id")),
            str(r.get("supplier_article", "") or ""),
            str(r.get("product_name", "") or ""),
            max(0, _int(r.get("planned_units"))),
            max(0, _int(r.get("actual_units"))),
            str(r.get("status", "Не начато") or "Не начато"),
            str(r.get("route", "") or ""),
            _date_text(r.get("dispatch_date")),
            _date_text(r.get("expected_arrival_date")),
            str(r.get("note", "") or ""),
            datetime.now().isoformat(timespec="seconds"),
        ))
    if not rows:
        return
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO execution_tasks(
                task_key,task_type,task_date,stage,nm_id,supplier_article,product_name,
                planned_units,actual_units,status,route,dispatch_date,expected_arrival_date,note,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(task_key) DO UPDATE SET
                task_type=excluded.task_type,
                task_date=excluded.task_date,
                stage=excluded.stage,
                nm_id=excluded.nm_id,
                supplier_article=excluded.supplier_article,
                product_name=excluded.product_name,
                planned_units=excluded.planned_units,
                actual_units=excluded.actual_units,
                status=excluded.status,
                route=excluded.route,
                dispatch_date=excluded.dispatch_date,
                expected_arrival_date=excluded.expected_arrival_date,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            rows,
        )

def sync_generated_execution_tasks(
    df: pd.DataFrame, task_type: str, from_date: Any
) -> None:
    """Synchronize generated future tasks without touching started/completed work.

    Current generated rows are upserted. Obsolete future rows are removed only
    when they have zero fact and still have the initial status ``Не начато``.
    This keeps manual execution history safe while allowing the Today page to
    immediately see the latest production calendar.
    """
    from .core import connect
    task_type = str(task_type or "").strip()
    if not task_type:
        return

    frame = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
    if not frame.empty:
        save_execution_tasks(frame)
        keep_keys = [
            str(value).strip()
            for value in frame.get("task_key", pd.Series(dtype=str)).tolist()
            if str(value).strip()
        ]
    else:
        keep_keys = []

    try:
        from_text = pd.to_datetime(from_date).date().isoformat()
    except Exception:
        from_text = datetime.now().date().isoformat()

    with connect() as conn:
        base_sql = """
            DELETE FROM execution_tasks
            WHERE task_type=?
              AND COALESCE(actual_units, 0)=0
              AND status='Не начато'
              AND (task_date IS NULL OR task_date>=?)
        """
        params: list[Any] = [task_type, from_text]
        if keep_keys:
            placeholders = ",".join("?" for _ in keep_keys)
            base_sql += f" AND task_key NOT IN ({placeholders})"
            params.extend(keep_keys)
        conn.execute(base_sql, params)

def _movement_date_text(value: Any) -> str | None:
    if pd.isna(value) or str(value).strip() in {"", "NaT", "None"}:
        return None
    try:
        return pd.to_datetime(value).date().isoformat()
    except Exception:
        return None

def _ensure_pipeline_row(
    conn: sqlite3.Connection,
    nm_id: int,
    supplier_article: str = "",
) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM product_pipeline WHERE nm_id=?", (int(nm_id),)).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO product_pipeline(
                nm_id,supplier_article,local_known,ready_units,inbound_known,inbound_units,
                inbound_date,note,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                int(nm_id), str(supplier_article or ""), 1, 0, 1, 0, None, "",
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        row = conn.execute("SELECT * FROM product_pipeline WHERE nm_id=?", (int(nm_id),)).fetchone()
    return row

def _recompute_inbound_date(conn: sqlite3.Connection, nm_id: int) -> None:
    row = conn.execute("SELECT inbound_units FROM product_pipeline WHERE nm_id=?", (int(nm_id),)).fetchone()
    inbound_units = int(row["inbound_units"] or 0) if row else 0
    if inbound_units <= 0:
        conn.execute(
            "UPDATE product_pipeline SET inbound_date=NULL, updated_at=? WHERE nm_id=?",
            (datetime.now().isoformat(timespec="seconds"), int(nm_id)),
        )
        return
    next_date = conn.execute(
        """
        SELECT MIN(expected_arrival_date) AS next_date
          FROM inventory_movements
         WHERE nm_id=?
           AND movement_type='dispatch'
           AND status='open'
           AND reversed_at IS NULL
           AND inbound_delta>0
           AND expected_arrival_date IS NOT NULL
        """,
        (int(nm_id),),
    ).fetchone()
    date_value = next_date["next_date"] if next_date and next_date["next_date"] else None
    conn.execute(
        "UPDATE product_pipeline SET inbound_date=?, updated_at=? WHERE nm_id=?",
        (date_value, datetime.now().isoformat(timespec="seconds"), int(nm_id)),
    )

def _apply_pipeline_delta(
    conn: sqlite3.Connection,
    nm_id: int,
    supplier_article: str,
    ready_delta: int,
    inbound_delta: int,
    expected_arrival_date: str | None = None,
) -> tuple[int, int]:
    row = _ensure_pipeline_row(conn, nm_id, supplier_article)
    current_ready = int(row["ready_units"] or 0)
    current_inbound = int(row["inbound_units"] or 0)
    new_ready = current_ready + int(ready_delta)
    new_inbound = current_inbound + int(inbound_delta)
    if new_ready < 0:
        raise ValueError(
            f"Недостаточно готовой продукции: доступно {current_ready}, требуется {abs(int(ready_delta))}."
        )
    if new_inbound < 0:
        raise ValueError(
            f"Недостаточно товара в пути: учтено {current_inbound}, требуется списать {abs(int(inbound_delta))}."
        )
    old_date = row["inbound_date"]
    inbound_date = old_date
    if new_inbound <= 0:
        inbound_date = None
    elif expected_arrival_date:
        if not old_date or str(expected_arrival_date) < str(old_date):
            inbound_date = expected_arrival_date
    conn.execute(
        """
        UPDATE product_pipeline
           SET supplier_article=CASE WHEN ?<>'' THEN ? ELSE supplier_article END,
               local_known=1,
               ready_units=?,
               inbound_known=1,
               inbound_units=?,
               inbound_date=?,
               updated_at=?
         WHERE nm_id=?
        """,
        (
            str(supplier_article or ""), str(supplier_article or ""), new_ready, new_inbound,
            inbound_date, datetime.now().isoformat(timespec="seconds"), int(nm_id),
        ),
    )
    return new_ready, new_inbound

def _material_key(value: Any) -> str:
    return str(value or "").strip().casefold()

def _apply_material_delta(
    conn: sqlite3.Connection,
    material_key: str,
    material_name: str,
    delta_meters: float,
) -> tuple[float, float]:
    key = _material_key(material_key or material_name)
    name = str(material_name or "").strip()
    if not key or not name:
        raise ValueError("Не указан материал/цвет для списания сырья.")
    row = conn.execute(
        "SELECT * FROM material_inventory_color WHERE material_key=?", (key,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Для материала «{name}» остаток сырья не заведён.")
    if int(row["balance_known"] or 0) != 1:
        raise ValueError(f"Для материала «{name}» остаток сырья не подтверждён.")
    try:
        unit = str(row["unit"] or "").strip() or "м"
    except (IndexError, KeyError):
        unit = "м"
    roll_length = max(float(row["roll_length"] or 25.5), 0.1)
    current = max(0.0, int(row["full_rolls"] or 0) * roll_length + float(row["partial_meters"] or 0))
    new_total = current + float(delta_meters or 0)
    if new_total < -0.005:
        raise ValueError(
            f"Недостаточно сырья «{name}»: доступно {current:.1f} {unit}, требуется {abs(float(delta_meters)):.1f} {unit}."
        )
    new_total = max(0.0, new_total)
    full_rolls = int(new_total // roll_length)
    partial = round(new_total - full_rolls * roll_length, 3)
    if partial >= roll_length - 0.001:
        full_rolls += 1
        partial = 0.0
    conn.execute(
        """
        UPDATE material_inventory_color
           SET material_name=?, full_rolls=?, partial_meters=?, updated_at=?
         WHERE material_key=?
        """,
        (name, full_rolls, partial, datetime.now().isoformat(timespec="seconds"), key),
    )
    return current, new_total

def _active_movement(
    conn: sqlite3.Connection,
    movement_type: str,
    source_task_key: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM inventory_movements
         WHERE movement_type=? AND source_task_key=? AND reversed_at IS NULL
         ORDER BY id DESC LIMIT 1
        """,
        (str(movement_type), str(source_task_key)),
    ).fetchone()

def _next_event_key(conn: sqlite3.Connection, movement_type: str, source_task_key: str) -> str:
    prefix = f"{movement_type}|{source_task_key}|v"
    count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM inventory_movements WHERE event_key LIKE ?",
        (prefix + "%",),
    ).fetchone()["cnt"]
    return f"{prefix}{int(count) + 1}"

def close_production_shift(task_date: Any) -> dict[str, Any]:
    from .core import connect
    from .fifo_finished_goods import _create_finished_goods_layer
    from .fifo_materials import _consume_material_fifo
    from .wip import _post_wip_packaging_conn, _wip_module_enabled
    date_value = _movement_date_text(task_date)
    result: dict[str, Any] = {
        "posted": 0, "skipped": 0, "errors": [], "units": 0,
        "meters": 0.0, "wip_units": 0, "cost_rub": 0.0,
    }
    if not date_value:
        result["errors"].append("Не указана дата смены.")
        return result
    with connect() as conn:
        use_wip = _wip_module_enabled(conn)
        tasks = conn.execute(
            """
            SELECT t.*, p.material_per_unit, p.material_name, p.blank_type, p.pack_size
              FROM execution_tasks t
              LEFT JOIN production_settings p ON p.nm_id=t.nm_id
             WHERE t.task_type='production' AND t.task_date=?
               AND t.actual_units>0
               AND t.status IN ('Изготовлено','Упаковано','Передано на отгрузку')
             ORDER BY t.nm_id, t.task_key
            """,
            (date_value,),
        ).fetchall()
        for task in tasks:
            source_key = str(task["task_key"])
            if _active_movement(conn, "production_receipt", source_key) or _active_movement(conn, "production_receipt_wip", source_key):
                result["skipped"] += 1
                continue
            qty = max(0, int(task["actual_units"] or 0))
            if qty <= 0:
                result["skipped"] += 1
                continue
            if use_wip and str(task["status"] or "") not in {"Упаковано", "Передано на отгрузку"}:
                result["skipped"] += 1
                continue

            if use_wip:
                conn.execute("SAVEPOINT post_production_wip_task")
                try:
                    posted = _post_wip_packaging_conn(
                        conn,
                        int(task["nm_id"] or 0),
                        str(task["supplier_article"] or ""),
                        str(task["product_name"] or ""),
                        qty,
                        date_value,
                        source_key,
                        str(task["note"] or ""),
                    )
                    conn.execute("RELEASE SAVEPOINT post_production_wip_task")
                    result["posted"] += int(posted.get("posted", 0) or 0)
                    result["skipped"] += int(posted.get("skipped", 0) or 0)
                    result["units"] += int(posted.get("units", 0) or 0)
                    result["wip_units"] += int(posted.get("wip_units", 0) or 0)
                    result["cost_rub"] += float(posted.get("cost_rub", 0) or 0)
                except Exception as exc:
                    conn.execute("ROLLBACK TO SAVEPOINT post_production_wip_task")
                    conn.execute("RELEASE SAVEPOINT post_production_wip_task")
                    result["errors"].append(f"{task['supplier_article']}: {exc}")
                continue

            material_name = str(task["material_name"] or "").strip()
            material_key = _material_key(material_name)
            material_rate = max(0.0, float(task["material_per_unit"] or 0))
            if not material_name or material_rate <= 0:
                result["errors"].append(
                    f"{task['supplier_article']}: не заполнены материал/цвет или норма расхода."
                )
                continue
            meters = round(qty * material_rate, 3)
            conn.execute("SAVEPOINT post_production_task")
            try:
                _apply_material_delta(conn, material_key, material_name, -meters)
                _apply_pipeline_delta(
                    conn, int(task["nm_id"] or 0), str(task["supplier_article"] or ""), qty, 0
                )
                event_key = _next_event_key(conn, "production_receipt", source_key)
                movement_cur = conn.execute(
                    """
                    INSERT INTO inventory_movements(
                        event_key,movement_type,nm_id,supplier_article,product_name,quantity,
                        ready_delta,inbound_delta,route,movement_date,expected_arrival_date,
                        source_task_key,reference_movement_id,status,note,created_at,reversed_at,
                        material_key,material_name,material_delta,material_cost_rub,unit_cost_rub
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event_key, "production_receipt",
                        int(task["nm_id"] or 0), str(task["supplier_article"] or ""),
                        str(task["product_name"] or ""), qty, qty, 0, "", date_value, None,
                        source_key, None, "applied",
                        f"Закрытие смены; списано {meters:.3f} м сырья «{material_name}»",
                        datetime.now().isoformat(timespec="seconds"), None,
                        material_key, material_name, -meters, 0.0, 0.0,
                    ),
                )
                movement_id = int(movement_cur.lastrowid)
                material_cost, allocations = _consume_material_fifo(
                    conn, movement_id, material_key, material_name, meters
                )
                cost_row = conn.execute("SELECT * FROM costs WHERE nm_id=?", (int(task["nm_id"] or 0),)).fetchone()
                packaging_unit = max(0.0, float(cost_row["packaging_cost_rub"] or 0)) if cost_row else 0.0
                if packaging_unit <= 0:
                    packaging_unit = 12.0
                labor_unit = max(0.0, float(cost_row["labor_cost_rub"] or 0)) if cost_row else 0.0
                other_unit = max(0.0, float(cost_row["other_cost_rub"] or 0)) if cost_row else 0.0
                packaging_total = round(packaging_unit * qty, 2)
                labor_total = round(labor_unit * qty, 2)
                other_total = round(other_unit * qty, 2)
                total_cost = round(material_cost + packaging_total + labor_total + other_total, 2)
                unit_cost = round(total_cost / qty, 4) if qty else 0.0
                allocation_note = ", ".join(
                    f"слой #{int(a['layer_id'])}: {a['meters']:.3f} м" for a in allocations
                )
                conn.execute(
                    """
                    UPDATE inventory_movements
                       SET material_cost_rub=?,unit_cost_rub=?,note=?
                     WHERE id=?
                    """,
                    (material_cost, unit_cost,
                     f"Закрытие смены; FIFO: {meters:.3f} м = {material_cost:.2f} ₽; себестоимость партии {unit_cost:.2f} ₽/компл.; {allocation_note}",
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
                    (movement_id, int(task["nm_id"] or 0), str(task["supplier_article"] or ""),
                     str(task["product_name"] or ""), date_value, qty, material_key, material_name,
                     meters, material_cost, packaging_total, labor_total, other_total, total_cost, unit_cost,
                     f"Фактическая партия по FIFO; {allocation_note}", datetime.now().isoformat(timespec="seconds")),
                )
                _create_finished_goods_layer(
                    conn, int(task["nm_id"] or 0), str(task["supplier_article"] or ""),
                    str(task["product_name"] or ""), "production", f"movement:{movement_id}",
                    date_value, qty, unit_cost, "ready",
                    f"Оприходовано из производственной партии #{movement_id}; сырьё списано по FIFO.",
                )
                conn.execute(
                    "UPDATE inventory_movements SET goods_cost_rub=?,goods_unit_cost_rub=? WHERE id=?",
                    (total_cost, unit_cost, movement_id),
                )
                conn.execute("RELEASE SAVEPOINT post_production_task")
                result["posted"] += 1
                result["units"] += qty
                result["meters"] += meters
                result["cost_rub"] += total_cost
            except Exception as exc:
                conn.execute("ROLLBACK TO SAVEPOINT post_production_task")
                conn.execute("RELEASE SAVEPOINT post_production_task")
                result["errors"].append(f"{task['supplier_article']}: {exc}")
    result["meters"] = round(float(result["meters"]), 3)
    result["cost_rub"] = round(float(result["cost_rub"]), 2)
    return result
