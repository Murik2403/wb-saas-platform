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


FINISHED_GOODS_LOCATIONS = {"ready": "ready_units", "inbound": "inbound_units", "wb": "wb_units"}

def _finished_location_column(location: str) -> str:
    key = str(location or "").strip().casefold()
    if key not in FINISHED_GOODS_LOCATIONS:
        raise ValueError(f"Неизвестное место хранения готовой продукции: {location}")
    return FINISHED_GOODS_LOCATIONS[key]

def _baseline_product_cost(conn: sqlite3.Connection, nm_id: int) -> float:
    row = conn.execute("SELECT cost_per_wb_unit FROM costs WHERE nm_id=?", (int(nm_id),)).fetchone()
    return max(0.0, float(row["cost_per_wb_unit"] or 0) if row else 0.0)

def _finished_layer_totals(conn: sqlite3.Connection, nm_id: int, location: str) -> tuple[int, float]:
    column = _finished_location_column(location)
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM({column}),0) AS units,
               COALESCE(SUM({column}*unit_cost_rub),0) AS amount
          FROM finished_goods_cost_layers
         WHERE nm_id=? AND status<>'reversed' AND {column}>0
        """,
        (int(nm_id),),
    ).fetchone()
    return max(0, int(row["units"] or 0) if row else 0), max(0.0, float(row["amount"] or 0) if row else 0.0)

def _physical_finished_units(conn: sqlite3.Connection, nm_id: int, location: str) -> tuple[bool, int]:
    loc = str(location or "").strip().casefold()
    if loc == "ready":
        row = conn.execute("SELECT local_known,ready_units FROM product_pipeline WHERE nm_id=?", (int(nm_id),)).fetchone()
        return (bool(row and int(row["local_known"] or 0) == 1), max(0, int(row["ready_units"] or 0) if row else 0))
    if loc == "inbound":
        row = conn.execute("SELECT inbound_known,inbound_units FROM product_pipeline WHERE nm_id=?", (int(nm_id),)).fetchone()
        return (bool(row and int(row["inbound_known"] or 0) == 1), max(0, int(row["inbound_units"] or 0) if row else 0))
    if loc == "wb":
        row = conn.execute(
            """
            SELECT COALESCE(SUM(quantity),0) AS units
              FROM stocks
             WHERE nm_id=? AND snapshot_at=(SELECT MAX(snapshot_at) FROM stocks)
            """,
            (int(nm_id),),
        ).fetchone()
        latest = conn.execute("SELECT MAX(snapshot_at) AS snapshot_at FROM stocks").fetchone()
        return (bool(latest and latest["snapshot_at"]), max(0, int(row["units"] or 0) if row else 0))
    raise ValueError(f"Неизвестное место хранения: {location}")

def _create_finished_goods_layer(
    conn: sqlite3.Connection,
    nm_id: int,
    supplier_article: str,
    product_name: str,
    source_type: str,
    source_ref: str,
    source_date: str | None,
    units: int,
    unit_cost_rub: float,
    location: str,
    note: str = "",
) -> int:
    qty = max(0, int(units or 0))
    if qty <= 0:
        raise ValueError("Нельзя создать нулевой слой готовой продукции.")
    column = _finished_location_column(location)
    balances = {"ready_units": 0, "inbound_units": 0, "wb_units": 0}
    balances[column] = qty
    rate = round(max(0.0, float(unit_cost_rub or 0)), 6)
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO finished_goods_cost_layers(
            nm_id,supplier_article,product_name,source_type,source_ref,source_date,
            original_units,ready_units,inbound_units,wb_units,unit_cost_rub,original_amount_rub,
            status,note,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'active',?,?,?)
        """,
        (
            int(nm_id), str(supplier_article or ""), str(product_name or ""),
            str(source_type or "opening"), str(source_ref), source_date,
            qty, balances["ready_units"], balances["inbound_units"], balances["wb_units"],
            rate, round(qty * rate, 2), str(note or ""), now, now,
        ),
    )
    return int(cur.lastrowid)

def _update_finished_layer_status(conn: sqlite3.Connection, layer_id: int) -> None:
    row = conn.execute(
        "SELECT ready_units,inbound_units,wb_units,status FROM finished_goods_cost_layers WHERE id=?",
        (int(layer_id),),
    ).fetchone()
    if row is None or str(row["status"] or "") == "reversed":
        return
    total = max(0, int(row["ready_units"] or 0)) + max(0, int(row["inbound_units"] or 0)) + max(0, int(row["wb_units"] or 0))
    conn.execute(
        "UPDATE finished_goods_cost_layers SET status=?,updated_at=? WHERE id=?",
        ("active" if total > 0 else "depleted", datetime.now().isoformat(timespec="seconds"), int(layer_id)),
    )

def _ensure_finished_goods_capacity(
    conn: sqlite3.Connection,
    nm_id: int,
    supplier_article: str,
    product_name: str,
    location: str,
    required_units: int,
) -> None:
    required = max(0, int(required_units or 0))
    layered, _ = _finished_layer_totals(conn, int(nm_id), location)
    if layered >= required:
        return
    known, physical = _physical_finished_units(conn, int(nm_id), location)
    if known and physical > layered:
        missing = physical - layered
        _create_finished_goods_layer(
            conn, int(nm_id), supplier_article, product_name,
            f"opening_{location}",
            f"opening_auto:{location}:{int(nm_id)}:{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            datetime.now().date().isoformat(), missing, _baseline_product_cost(conn, int(nm_id)), location,
            "Автоматически досоздано по подтверждённому физическому остатку.",
        )
        layered += missing
    if layered < required:
        raise ValueError(
            f"В слоях готовой продукции ({location}) доступно {layered} ед., требуется {required}. "
            "Сначала подтвердите остаток или инициализируйте FIFO готовой продукции."
        )

def _cost_pending_wb_units(conn: sqlite3.Connection, nm_id: int) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(wb_units),0) AS units
          FROM finished_goods_cost_layers
         WHERE nm_id=? AND status<>'reversed' AND source_type='reconstruction_cost_pending' AND wb_units>0
        """,
        (int(nm_id),),
    ).fetchone()
    return max(0, int(row["units"] or 0) if row else 0)

def _regular_wb_fifo_units(conn: sqlite3.Connection, nm_id: int) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(wb_units),0) AS units
          FROM finished_goods_cost_layers
         WHERE nm_id=? AND status<>'reversed' AND source_type<>'reconstruction_cost_pending' AND wb_units>0
        """,
        (int(nm_id),),
    ).fetchone()
    return max(0, int(row["units"] or 0) if row else 0)

def _move_finished_goods_fifo(
    conn: sqlite3.Connection,
    movement_id: int,
    nm_id: int,
    supplier_article: str,
    product_name: str,
    units: int,
    from_location: str,
    to_location: str | None,
) -> tuple[float, list[dict[str, float]]]:
    qty = max(0, int(units or 0))
    if qty <= 0:
        return 0.0, []
    if str(from_location or "").strip().casefold() == "wb":
        pending = _cost_pending_wb_units(conn, int(nm_id))
        regular = _regular_wb_fifo_units(conn, int(nm_id))
        if pending > 0 and regular < qty:
            raise ValueError(
                f"COST_PENDING: для nmID {int(nm_id)} есть {pending} ед. с неопределённой себестоимостью. "
                "Сначала разрешите cost basis; синтетическая ставка запрещена."
            )
    from_col = _finished_location_column(from_location)
    to_col = _finished_location_column(to_location) if to_location in FINISHED_GOODS_LOCATIONS else None
    _ensure_finished_goods_capacity(conn, nm_id, supplier_article, product_name, from_location, qty)
    pending_filter = " AND source_type<>'reconstruction_cost_pending'" if str(from_location or "").strip().casefold() == "wb" else ""
    rows = conn.execute(
        f"""
        SELECT * FROM finished_goods_cost_layers
         WHERE nm_id=? AND status<>'reversed' AND {from_col}>0 {pending_filter}
         ORDER BY COALESCE(source_date,'1900-01-01'),id
        """,
        (int(nm_id),),
    ).fetchall()
    remaining = qty
    amount_total = 0.0
    allocations: list[dict[str, float]] = []
    now = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        if remaining <= 0:
            break
        available = max(0, int(row[from_col] or 0))
        take = min(available, remaining)
        if take <= 0:
            continue
        amount = round(take * float(row["unit_cost_rub"] or 0), 2)
        assignments = [f"{from_col}={from_col}-?"]
        params: list[Any] = [take]
        if to_col:
            assignments.append(f"{to_col}={to_col}+?")
            params.append(take)
        assignments.append("updated_at=?")
        params.append(now)
        params.append(int(row["id"]))
        conn.execute(
            f"UPDATE finished_goods_cost_layers SET {','.join(assignments)} WHERE id=?",
            params,
        )
        conn.execute(
            """
            INSERT INTO finished_goods_fifo_allocations(
                movement_id,layer_id,units,amount_rub,from_location,to_location,status,created_at
            ) VALUES (?,?,?,?,?,?,'applied',?)
            """,
            (int(movement_id), int(row["id"]), take, amount, from_location, str(to_location or "out"), now),
        )
        _update_finished_layer_status(conn, int(row["id"]))
        allocations.append({"layer_id": float(row["id"]), "units": float(take), "amount_rub": amount})
        amount_total += amount
        remaining -= take
    if remaining > 0:
        raise ValueError(f"Не удалось переместить {remaining} ед. готовой продукции по FIFO.")
    return round(amount_total, 2), allocations

def _move_dispatch_allocation_to_wb(
    conn: sqlite3.Connection,
    receipt_movement_id: int,
    dispatch_movement_id: int,
    units: int,
) -> tuple[float, list[dict[str, float]]]:
    qty = max(0, int(units or 0))
    rows = conn.execute(
        """
        SELECT a.*,l.inbound_units,l.unit_cost_rub
          FROM finished_goods_fifo_allocations a
          JOIN finished_goods_cost_layers l ON l.id=a.layer_id
         WHERE a.movement_id=? AND a.status='applied' AND a.to_location='inbound'
         ORDER BY a.id
        """,
        (int(dispatch_movement_id),),
    ).fetchall()
    remaining = qty
    total = 0.0
    allocations: list[dict[str, float]] = []
    now = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        if remaining <= 0:
            break
        take = min(max(0, int(row["units"] or 0)), max(0, int(row["inbound_units"] or 0)), remaining)
        if take <= 0:
            continue
        amount = round(take * float(row["unit_cost_rub"] or 0), 2)
        conn.execute(
            "UPDATE finished_goods_cost_layers SET inbound_units=inbound_units-?,wb_units=wb_units+?,updated_at=? WHERE id=?",
            (take, take, now, int(row["layer_id"])),
        )
        conn.execute(
            """
            INSERT INTO finished_goods_fifo_allocations(
                movement_id,layer_id,units,amount_rub,from_location,to_location,status,created_at
            ) VALUES (?,?,?,?, 'inbound','wb','applied',?)
            """,
            (int(receipt_movement_id), int(row["layer_id"]), take, amount, now),
        )
        _update_finished_layer_status(conn, int(row["layer_id"]))
        allocations.append({"layer_id": float(row["layer_id"]), "units": float(take), "amount_rub": amount})
        total += amount
        remaining -= take
    if remaining > 0:
        raise ValueError(f"В поставке не найдено {remaining} ед. послойной себестоимости.")
    return round(total, 2), allocations

def _reverse_finished_goods_allocations(conn: sqlite3.Connection, movement_id: int) -> None:
    rows = conn.execute(
        """
        SELECT * FROM finished_goods_fifo_allocations
         WHERE movement_id=? AND status='applied' AND reversed_at IS NULL
         ORDER BY id DESC
        """,
        (int(movement_id),),
    ).fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        from_loc = str(row["from_location"] or "")
        to_loc = str(row["to_location"] or "")
        qty = max(0, int(row["units"] or 0))
        assignments: list[str] = []
        params: list[Any] = []
        if to_loc in FINISHED_GOODS_LOCATIONS:
            to_col = _finished_location_column(to_loc)
            current = conn.execute(f"SELECT {to_col} AS units FROM finished_goods_cost_layers WHERE id=?", (int(row["layer_id"]),)).fetchone()
            available = max(0, int(current["units"] or 0) if current else 0)
            if available < qty:
                raise ValueError("Нельзя отменить движение: часть этой партии уже ушла следующим этапом.")
            assignments.append(f"{to_col}={to_col}-?")
            params.append(qty)
        if from_loc in FINISHED_GOODS_LOCATIONS:
            from_col = _finished_location_column(from_loc)
            assignments.append(f"{from_col}={from_col}+?")
            params.append(qty)
        assignments.append("updated_at=?")
        params.append(now)
        params.append(int(row["layer_id"]))
        conn.execute(f"UPDATE finished_goods_cost_layers SET {','.join(assignments)} WHERE id=?", params)
        conn.execute(
            "UPDATE finished_goods_fifo_allocations SET status='reversed',reversed_at=? WHERE id=?",
            (now, int(row["id"])),
        )
        _update_finished_layer_status(conn, int(row["layer_id"]))

def _reverse_finished_goods_source_layer(conn: sqlite3.Connection, movement_id: int) -> None:
    layer = conn.execute(
        "SELECT * FROM finished_goods_cost_layers WHERE source_ref=? AND status<>'reversed'",
        (f"movement:{int(movement_id)}",),
    ).fetchone()
    if layer is None:
        return
    active_alloc = conn.execute(
        "SELECT COUNT(*) AS n FROM finished_goods_fifo_allocations WHERE layer_id=? AND status='applied'",
        (int(layer["id"]),),
    ).fetchone()
    if int(active_alloc["n"] or 0) > 0:
        raise ValueError("Сначала отмените отгрузку/приёмку, использующую эту партию.")
    total = int(layer["ready_units"] or 0) + int(layer["inbound_units"] or 0) + int(layer["wb_units"] or 0)
    if total != int(layer["original_units"] or 0):
        raise ValueError("Слой готовой продукции уже частично использован; сначала отмените последующие движения.")
    conn.execute(
        """
        UPDATE finished_goods_cost_layers
           SET ready_units=0,inbound_units=0,wb_units=0,status='reversed',updated_at=?
         WHERE id=?
        """,
        (datetime.now().isoformat(timespec="seconds"), int(layer["id"])),
    )

def initialize_finished_goods_fifo(reconcile_wb: bool = False) -> dict[str, Any]:
    """Create opening cost layers for confirmed ready/inbound/WB balances and align WB layers to the latest snapshot."""
    from .core import connect
    from .production import _active_movement, _next_event_key
    result: dict[str, Any] = {"created": 0, "units": 0, "wb_consumed": 0, "warnings": []}
    with connect() as conn:
        ids = conn.execute(
            """
            SELECT nm_id FROM products_catalog WHERE nm_id>0
            UNION SELECT nm_id FROM product_pipeline WHERE nm_id>0
            UNION SELECT nm_id FROM costs WHERE nm_id>0
            UNION SELECT nm_id FROM stocks WHERE nm_id>0
            """
        ).fetchall()
        latest = conn.execute("SELECT MAX(snapshot_at) AS snapshot_at FROM stocks").fetchone()
        snapshot = str(latest["snapshot_at"] or "") if latest else ""
        for item in ids:
            nm_id = int(item["nm_id"] or 0)
            meta = conn.execute(
                """
                SELECT COALESCE(pc.supplier_article,c.supplier_article,'') AS supplier_article,
                       COALESCE(pc.product_name,'') AS product_name
                  FROM (SELECT ? AS nm_id) i
                  LEFT JOIN products_catalog pc ON pc.nm_id=i.nm_id
                  LEFT JOIN costs c ON c.nm_id=i.nm_id
                """,
                (nm_id,),
            ).fetchone()
            article = str(meta["supplier_article"] or "") if meta else ""
            product = str(meta["product_name"] or "") if meta else ""
            baseline = _baseline_product_cost(conn, nm_id)
            for location in ("ready", "inbound", "wb"):
                known, physical = _physical_finished_units(conn, nm_id, location)
                if not known:
                    continue
                layered, _ = _finished_layer_totals(conn, nm_id, location)
                diff = physical - layered
                if diff > 0:
                    _create_finished_goods_layer(
                        conn, nm_id, article, product, f"opening_{location}",
                        f"opening:{location}:{nm_id}:{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
                        datetime.now().date().isoformat(), diff, baseline, location,
                        "Стартовый/досинхронизированный остаток готовой продукции по базовой себестоимости.",
                    )
                    result["created"] += 1
                    result["units"] += diff
                elif diff < 0:
                    if location == "wb" and reconcile_wb:
                        qty = abs(diff)
                        source_key = f"wb_snapshot:{snapshot}:{nm_id}:{layered}->{physical}"
                        if _active_movement(conn, "wb_cost_reconciliation", source_key) is None:
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
                                    _next_event_key(conn, "wb_cost_reconciliation", source_key), "wb_cost_reconciliation",
                                    nm_id, article, product, qty, 0, 0, "WB snapshot",
                                    datetime.now().date().isoformat(), None, source_key, None, "applied",
                                    "Списание стоимости по уменьшению фактического остатка WB; управленческая сверка.",
                                    datetime.now().isoformat(timespec="seconds"), None, 0.0, 0.0,
                                ),
                            )
                            movement_id = int(cur.lastrowid)
                            cost, _ = _move_finished_goods_fifo(conn, movement_id, nm_id, article, product, qty, "wb", None)
                            conn.execute(
                                "UPDATE inventory_movements SET goods_cost_rub=?,goods_unit_cost_rub=?,note=? WHERE id=?",
                                (cost, round(cost/qty, 4) if qty else 0.0,
                                 "Сверка FIFO готовой продукции с последним остатком WB; не является бухгалтерским COGS.", movement_id),
                            )
                            result["wb_consumed"] += qty
                    else:
                        result["warnings"].append(
                            f"{article or nm_id}, {location}: в слоях на {abs(diff)} ед. больше физического остатка."
                        )
    return result

def read_finished_goods_cost_layers(active_only: bool = False) -> pd.DataFrame:
    from .core import connect
    where = "WHERE status<>'reversed' AND (ready_units+inbound_units+wb_units)>0" if active_only else ""
    with connect() as conn:
        return pd.read_sql_query(
            f"SELECT * FROM finished_goods_cost_layers {where} ORDER BY nm_id,COALESCE(source_date,'1900-01-01'),id",
            conn,
        )

def read_finished_goods_fifo_allocations(limit: int = 500) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            SELECT a.*,l.nm_id,l.supplier_article,l.product_name,l.source_type,l.source_ref,l.unit_cost_rub
              FROM finished_goods_fifo_allocations a
              JOIN finished_goods_cost_layers l ON l.id=a.layer_id
             ORDER BY a.id DESC LIMIT ?
            """,
            conn, params=(max(1, int(limit)),),
        )

def read_finished_goods_fifo_summary() -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            WITH latest_stock AS (SELECT MAX(snapshot_at) AS snapshot_at FROM stocks),
            wb AS (
                SELECT s.nm_id,SUM(COALESCE(s.quantity,0)) AS wb_physical
                  FROM stocks s JOIN latest_stock l ON s.snapshot_at=l.snapshot_at GROUP BY s.nm_id
            ), goods AS (
                SELECT nm_id,
                       SUM(CASE WHEN status<>'reversed' THEN ready_units ELSE 0 END) AS ready_layer_units,
                       SUM(CASE WHEN status<>'reversed' THEN ready_units*unit_cost_rub ELSE 0 END) AS ready_amount,
                       SUM(CASE WHEN status<>'reversed' THEN inbound_units ELSE 0 END) AS inbound_layer_units,
                       SUM(CASE WHEN status<>'reversed' THEN inbound_units*unit_cost_rub ELSE 0 END) AS inbound_amount,
                       SUM(CASE WHEN status<>'reversed' THEN wb_units ELSE 0 END) AS wb_layer_units,
                       SUM(CASE WHEN status<>'reversed' THEN wb_units*unit_cost_rub ELSE 0 END) AS wb_amount,
                       COUNT(CASE WHEN status='active' THEN 1 END) AS active_layers
                  FROM finished_goods_cost_layers GROUP BY nm_id
            ), ids AS (
                SELECT nm_id FROM products_catalog UNION SELECT nm_id FROM product_pipeline
                UNION SELECT nm_id FROM wb UNION SELECT nm_id FROM goods UNION SELECT nm_id FROM costs
            )
            SELECT i.nm_id,COALESCE(pc.supplier_article,c.supplier_article,'') AS supplier_article,
                   COALESCE(pc.product_name,'') AS product_name,
                   CASE WHEN COALESCE(pp.local_known,0)=1 THEN COALESCE(pp.ready_units,0) ELSE 0 END AS ready_physical,
                   COALESCE(g.ready_layer_units,0) AS ready_layer_units,
                   CASE WHEN COALESCE(g.ready_layer_units,0)>0 THEN g.ready_amount/g.ready_layer_units ELSE 0 END AS ready_rate_rub,
                   CASE WHEN COALESCE(pp.inbound_known,0)=1 THEN COALESCE(pp.inbound_units,0) ELSE 0 END AS inbound_physical,
                   COALESCE(g.inbound_layer_units,0) AS inbound_layer_units,
                   CASE WHEN COALESCE(g.inbound_layer_units,0)>0 THEN g.inbound_amount/g.inbound_layer_units ELSE 0 END AS inbound_rate_rub,
                   COALESCE(w.wb_physical,0) AS wb_physical,COALESCE(g.wb_layer_units,0) AS wb_layer_units,
                   CASE WHEN COALESCE(g.wb_layer_units,0)>0 THEN g.wb_amount/g.wb_layer_units ELSE 0 END AS wb_rate_rub,
                   COALESCE(g.active_layers,0) AS active_layers,
                   (CASE WHEN COALESCE(pp.local_known,0)=1 THEN COALESCE(pp.ready_units,0) ELSE 0 END)-COALESCE(g.ready_layer_units,0) AS ready_difference,
                   (CASE WHEN COALESCE(pp.inbound_known,0)=1 THEN COALESCE(pp.inbound_units,0) ELSE 0 END)-COALESCE(g.inbound_layer_units,0) AS inbound_difference,
                   COALESCE(w.wb_physical,0)-COALESCE(g.wb_layer_units,0) AS wb_difference
              FROM ids i
              LEFT JOIN products_catalog pc ON pc.nm_id=i.nm_id
              LEFT JOIN costs c ON c.nm_id=i.nm_id
              LEFT JOIN product_pipeline pp ON pp.nm_id=i.nm_id
              LEFT JOIN wb w ON w.nm_id=i.nm_id
              LEFT JOIN goods g ON g.nm_id=i.nm_id
             WHERE i.nm_id>0
             ORDER BY ABS(COALESCE(w.wb_physical,0)-COALESCE(g.wb_layer_units,0)) DESC,
                      ABS((CASE WHEN COALESCE(pp.local_known,0)=1 THEN COALESCE(pp.ready_units,0) ELSE 0 END)-COALESCE(g.ready_layer_units,0)) DESC,
                      supplier_article
            """,
            conn,
        )

def read_inventory_valuation() -> pd.DataFrame:
    """Current quantity/value snapshot using location-specific finished-goods FIFO where available."""
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            WITH latest_stock AS (
                SELECT MAX(snapshot_at) AS snapshot_at FROM stocks
            ), wb AS (
                SELECT s.nm_id,SUM(COALESCE(s.quantity,0)) AS wb_units
                  FROM stocks s JOIN latest_stock l ON s.snapshot_at=l.snapshot_at
                 GROUP BY s.nm_id
            ), batch AS (
                SELECT nm_id,
                       SUM(CASE WHEN status='active' THEN produced_units ELSE 0 END) AS produced_units,
                       SUM(CASE WHEN status='active' THEN total_cost_rub ELSE 0 END) AS batch_cost,
                       CASE WHEN SUM(CASE WHEN status='active' THEN produced_units ELSE 0 END)>0
                            THEN SUM(CASE WHEN status='active' THEN total_cost_rub ELSE 0 END)
                                 /SUM(CASE WHEN status='active' THEN produced_units ELSE 0 END)
                            ELSE 0 END AS actual_unit_cost,
                       MAX(CASE WHEN status='active' THEN batch_date END) AS latest_batch_date
                  FROM production_cost_batches GROUP BY nm_id
            ), goods AS (
                SELECT nm_id,
                       SUM(CASE WHEN status<>'reversed' THEN ready_units ELSE 0 END) AS ready_layer_units,
                       SUM(CASE WHEN status<>'reversed' THEN ready_units*unit_cost_rub ELSE 0 END) AS ready_layer_amount,
                       SUM(CASE WHEN status<>'reversed' THEN inbound_units ELSE 0 END) AS inbound_layer_units,
                       SUM(CASE WHEN status<>'reversed' THEN inbound_units*unit_cost_rub ELSE 0 END) AS inbound_layer_amount,
                       SUM(CASE WHEN status<>'reversed' THEN wb_units ELSE 0 END) AS wb_layer_units,
                       SUM(CASE WHEN status<>'reversed' THEN wb_units*unit_cost_rub ELSE 0 END) AS wb_layer_amount,
                       COUNT(CASE WHEN status='active' THEN 1 END) AS active_finished_layers
                  FROM finished_goods_cost_layers GROUP BY nm_id
            ), ids AS (
                SELECT nm_id FROM products_catalog
                UNION SELECT nm_id FROM wb
                UNION SELECT nm_id FROM product_pipeline
                UNION SELECT nm_id FROM costs
                UNION SELECT nm_id FROM batch
                UNION SELECT nm_id FROM goods
            ), base AS (
                SELECT i.nm_id,
                       COALESCE(pc.supplier_article,c.supplier_article,'') AS supplier_article,
                       COALESCE(pc.product_name,'') AS product_name,
                       CASE WHEN COALESCE(ps.enabled,0)=1 THEN 'Собственное производство' ELSE 'Закупаемый / прочий товар' END AS source_type,
                       COALESCE(w.wb_units,0) AS wb_units,
                       CASE WHEN COALESCE(pp.local_known,0)=1 THEN COALESCE(pp.ready_units,0) ELSE 0 END AS ready_units,
                       CASE WHEN COALESCE(pp.inbound_known,0)=1 THEN COALESCE(pp.inbound_units,0) ELSE 0 END AS inbound_units,
                       COALESCE(pp.local_known,0) AS ready_known,
                       COALESCE(pp.inbound_known,0) AS inbound_known,
                       COALESCE(c.cost_per_wb_unit,0) AS baseline_unit_cost,
                       COALESCE(b.actual_unit_cost,0) AS actual_batch_unit_cost,
                       COALESCE(b.produced_units,0) AS actual_profile_units,
                       COALESCE(b.latest_batch_date,'') AS latest_batch_date,
                       COALESCE(g.ready_layer_units,0) AS ready_layer_units,
                       COALESCE(g.ready_layer_amount,0) AS ready_layer_amount,
                       COALESCE(g.inbound_layer_units,0) AS inbound_layer_units,
                       COALESCE(g.inbound_layer_amount,0) AS inbound_layer_amount,
                       COALESCE(g.wb_layer_units,0) AS wb_layer_units,
                       COALESCE(g.wb_layer_amount,0) AS wb_layer_amount,
                       COALESCE(g.active_finished_layers,0) AS active_finished_layers
                  FROM ids i
                  LEFT JOIN products_catalog pc ON pc.nm_id=i.nm_id
                  LEFT JOIN costs c ON c.nm_id=i.nm_id
                  LEFT JOIN production_settings ps ON ps.nm_id=i.nm_id
                  LEFT JOIN wb w ON w.nm_id=i.nm_id
                  LEFT JOIN product_pipeline pp ON pp.nm_id=i.nm_id
                  LEFT JOIN batch b ON b.nm_id=i.nm_id
                  LEFT JOIN goods g ON g.nm_id=i.nm_id
                 WHERE i.nm_id>0
            ), values_calc AS (
                SELECT base.*,
                       CASE WHEN wb_layer_units>0 THEN wb_layer_amount/wb_layer_units ELSE baseline_unit_cost END AS wb_fifo_unit_cost,
                       CASE WHEN ready_layer_units>0 THEN ready_layer_amount/ready_layer_units ELSE baseline_unit_cost END AS ready_fifo_unit_cost,
                       CASE WHEN inbound_layer_units>0 THEN inbound_layer_amount/inbound_layer_units ELSE baseline_unit_cost END AS inbound_fifo_unit_cost,
                       CASE WHEN wb_layer_units>0
                            THEN MIN(wb_units,wb_layer_units)*(wb_layer_amount/wb_layer_units)+MAX(wb_units-wb_layer_units,0)*baseline_unit_cost
                            ELSE wb_units*baseline_unit_cost END AS wb_value_rub,
                       CASE WHEN ready_layer_units>0
                            THEN MIN(ready_units,ready_layer_units)*(ready_layer_amount/ready_layer_units)+MAX(ready_units-ready_layer_units,0)*baseline_unit_cost
                            ELSE ready_units*baseline_unit_cost END AS ready_value_rub,
                       CASE WHEN inbound_layer_units>0
                            THEN MIN(inbound_units,inbound_layer_units)*(inbound_layer_amount/inbound_layer_units)+MAX(inbound_units-inbound_layer_units,0)*baseline_unit_cost
                            ELSE inbound_units*baseline_unit_cost END AS inbound_value_rub
                  FROM base
            )
            SELECT values_calc.*,
                   CASE WHEN (wb_units+ready_units+inbound_units)>0
                        THEN (wb_value_rub+ready_value_rub+inbound_value_rub)/(wb_units+ready_units+inbound_units)
                        WHEN actual_batch_unit_cost>0 THEN actual_batch_unit_cost ELSE baseline_unit_cost END AS valuation_unit_cost,
                   CASE WHEN (wb_layer_units+ready_layer_units+inbound_layer_units)>0 THEN 'FIFO готовой продукции'
                        WHEN actual_batch_unit_cost>0 THEN 'Фактическая средняя партий'
                        ELSE 'Базовая себестоимость' END AS cost_source,
                   CASE WHEN wb_units>0 THEN MIN(wb_units,wb_layer_units)*100.0/wb_units ELSE 0 END AS wb_fifo_coverage_pct,
                   CASE WHEN ready_units>0 THEN MIN(ready_units,ready_layer_units)*100.0/ready_units ELSE 0 END AS ready_fifo_coverage_pct,
                   CASE WHEN inbound_units>0 THEN MIN(inbound_units,inbound_layer_units)*100.0/inbound_units ELSE 0 END AS inbound_fifo_coverage_pct
              FROM values_calc
             ORDER BY (wb_units+ready_units+inbound_units) DESC,supplier_article
            """,
            conn,
        )
