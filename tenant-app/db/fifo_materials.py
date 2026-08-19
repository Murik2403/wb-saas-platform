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


DEFAULT_OPENING_MATERIAL_RATE_RUB_M = 402.68

def _fifo_opening_rate(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT value FROM app_meta WHERE key='fifo_opening_rate_rub_m'").fetchone()
    try:
        value = float(row["value"]) if row else DEFAULT_OPENING_MATERIAL_RATE_RUB_M
    except Exception:
        value = DEFAULT_OPENING_MATERIAL_RATE_RUB_M
    return max(0.0, value)

def set_fifo_opening_rate(rate_rub_m: float) -> None:
    from .core import connect
    rate = max(0.0, float(rate_rub_m or 0))
    if rate <= 0:
        raise ValueError("Ставка старого сырья должна быть больше нуля.")
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO app_meta(key,value,updated_at) VALUES ('fifo_opening_rate_rub_m',?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
            """,
            (f"{rate:.6f}", now),
        )

def get_fifo_opening_rate() -> float:
    from .core import connect
    with connect() as conn:
        return _fifo_opening_rate(conn)

def _physical_material_total(row: sqlite3.Row | dict[str, Any] | None) -> float:
    if row is None:
        return 0.0
    return max(
        0.0,
        int(row["full_rolls"] or 0) * max(0.1, float(row["roll_length"] or 25.5))
        + float(row["partial_meters"] or 0),
    )

def _active_layer_total(conn: sqlite3.Connection, material_key: str) -> float:
    from .production import _material_key
    row = conn.execute(
        """
        SELECT COALESCE(SUM(remaining_meters),0) AS meters
          FROM material_cost_layers
         WHERE material_key=? AND status='active' AND remaining_meters>0.0005
        """,
        (_material_key(material_key),),
    ).fetchone()
    return max(0.0, float(row["meters"] or 0) if row else 0.0)

def _create_material_layer(
    conn: sqlite3.Connection,
    material_key: str,
    material_name: str,
    source_type: str,
    source_ref: str,
    source_date: str | None,
    meters: float,
    unit_cost_rub_m: float,
    note: str = "",
) -> int:
    from .production import _material_key
    meters = round(max(0.0, float(meters or 0)), 6)
    rate = round(max(0.0, float(unit_cost_rub_m or 0)), 6)
    if meters <= 0.0005:
        raise ValueError("Нельзя создать нулевой слой сырья.")
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO material_cost_layers(
            material_key,material_name,source_type,source_ref,source_date,
            original_meters,remaining_meters,unit_cost_rub_m,original_amount_rub,
            status,note,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,'active',?,?,?)
        """,
        (
            _material_key(material_key or material_name), str(material_name or "").strip(),
            str(source_type or "opening"), str(source_ref), source_date,
            meters, meters, rate, round(meters * rate, 2), str(note or ""), now, now,
        ),
    )
    return int(cur.lastrowid)

def initialize_material_fifo(opening_rate_rub_m: float | None = None) -> dict[str, Any]:
    """Create opening FIFO layers for confirmed physical stock not yet represented by layers."""
    from .core import connect
    from .production import _material_key
    result: dict[str, Any] = {"created": 0, "meters": 0.0, "materials": 0, "warnings": []}
    with connect() as conn:
        if opening_rate_rub_m is not None:
            rate = max(0.0, float(opening_rate_rub_m or 0))
            if rate <= 0:
                raise ValueError("Ставка старого сырья должна быть больше нуля.")
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                """
                INSERT INTO app_meta(key,value,updated_at) VALUES ('fifo_opening_rate_rub_m',?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
                """,
                (f"{rate:.6f}", now),
            )
        rate = _fifo_opening_rate(conn)
        rows = conn.execute(
            "SELECT * FROM material_inventory_color WHERE balance_known=1 ORDER BY material_name"
        ).fetchall()
        for row in rows:
            key = _material_key(row["material_key"] or row["material_name"])
            name = str(row["material_name"] or "").strip()
            physical = _physical_material_total(row)
            layered = _active_layer_total(conn, key)
            diff = round(physical - layered, 6)
            if diff > 0.0005:
                source_ref = f"opening:{key}:{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
                _create_material_layer(
                    conn, key, name, "opening", source_ref, datetime.now().date().isoformat(),
                    diff, rate,
                    "Стартовый остаток до послойного учёта; ставка рассчитана из исторической себестоимости.",
                )
                result["created"] += 1
                result["materials"] += 1
                result["meters"] += diff
            elif diff < -0.0005:
                result["warnings"].append(
                    f"{name}: в слоях на {abs(diff):.1f} м больше, чем в физическом остатке. Проверьте ручную корректировку склада."
                )
    result["meters"] = round(float(result["meters"]), 3)
    return result

def _ensure_fifo_capacity(
    conn: sqlite3.Connection,
    material_key: str,
    material_name: str,
    required_meters: float,
) -> None:
    from .production import _material_key
    key = _material_key(material_key or material_name)
    required = max(0.0, float(required_meters or 0))
    layered = _active_layer_total(conn, key)
    if layered + 0.0005 >= required:
        return
    inventory = conn.execute(
        "SELECT * FROM material_inventory_color WHERE material_key=?", (key,)
    ).fetchone()
    physical = _physical_material_total(inventory)
    missing_layer = max(0.0, physical - layered)
    if missing_layer > 0.0005:
        _create_material_layer(
            conn, key, material_name, "opening_auto",
            f"opening_auto:{key}:{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            datetime.now().date().isoformat(), missing_layer, _fifo_opening_rate(conn),
            "Автоматически досоздано для ранее учтённого физического остатка.",
        )
        layered += missing_layer
    if layered + 0.0005 < required:
        raise ValueError(
            f"Для материала «{material_name}» в FIFO-слоях доступно {layered:.1f} м, требуется {required:.1f} м."
        )

def _consume_material_fifo(
    conn: sqlite3.Connection,
    movement_id: int,
    material_key: str,
    material_name: str,
    meters: float,
) -> tuple[float, list[dict[str, float]]]:
    from .production import _material_key
    required = round(max(0.0, float(meters or 0)), 6)
    if required <= 0.0005:
        return 0.0, []
    key = _material_key(material_key or material_name)
    _ensure_fifo_capacity(conn, key, material_name, required)
    layers = conn.execute(
        """
        SELECT * FROM material_cost_layers
         WHERE material_key=? AND status='active' AND remaining_meters>0.0005
         ORDER BY COALESCE(source_date,'1900-01-01'),id
        """,
        (key,),
    ).fetchall()
    remaining = required
    total_cost = 0.0
    allocations: list[dict[str, float]] = []
    now = datetime.now().isoformat(timespec="seconds")
    for layer in layers:
        if remaining <= 0.0005:
            break
        available = max(0.0, float(layer["remaining_meters"] or 0))
        take = min(available, remaining)
        if take <= 0.0005:
            continue
        amount = round(take * float(layer["unit_cost_rub_m"] or 0), 2)
        new_remaining = max(0.0, available - take)
        conn.execute(
            "UPDATE material_cost_layers SET remaining_meters=?,status=?,updated_at=? WHERE id=?",
            (new_remaining, "depleted" if new_remaining <= 0.0005 else "active", now, int(layer["id"])),
        )
        conn.execute(
            """
            INSERT INTO material_fifo_consumptions(movement_id,layer_id,meters,amount_rub,status,created_at)
            VALUES (?,?,?,?, 'applied', ?)
            """,
            (int(movement_id), int(layer["id"]), take, amount, now),
        )
        allocations.append({"layer_id": float(layer["id"]), "meters": take, "amount_rub": amount})
        total_cost += amount
        remaining -= take
    if remaining > 0.001:
        raise ValueError(f"Не удалось списать {remaining:.3f} м сырья по FIFO.")
    return round(total_cost, 2), allocations

def _reverse_fifo_consumption(conn: sqlite3.Connection, movement_id: int) -> None:
    rows = conn.execute(
        """
        SELECT c.*,l.remaining_meters
          FROM material_fifo_consumptions c
          JOIN material_cost_layers l ON l.id=c.layer_id
         WHERE c.movement_id=? AND c.status='applied' AND c.reversed_at IS NULL
         ORDER BY c.id DESC
        """,
        (int(movement_id),),
    ).fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        conn.execute(
            "UPDATE material_cost_layers SET remaining_meters=remaining_meters+?,status='active',updated_at=? WHERE id=?",
            (float(row["meters"] or 0), now, int(row["layer_id"])),
        )
        conn.execute(
            "UPDATE material_fifo_consumptions SET status='reversed',reversed_at=? WHERE id=?",
            (now, int(row["id"])),
        )
    conn.execute(
        "UPDATE production_cost_batches SET status='reversed',reversed_at=? WHERE movement_id=? AND reversed_at IS NULL",
        (now, int(movement_id)),
    )

def _reverse_procurement_material_layer(conn: sqlite3.Connection, movement_id: int) -> None:
    layers = conn.execute(
        """
        SELECT * FROM material_cost_layers
         WHERE source_type='procurement' AND source_ref=? AND status<>'reversed'
        """,
        (f"movement:{int(movement_id)}",),
    ).fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    for layer in layers:
        original = float(layer["original_meters"] or 0)
        remaining = float(layer["remaining_meters"] or 0)
        if remaining + 0.0005 < original:
            raise ValueError(
                f"Нельзя отменить приёмку: из слоя «{layer['material_name']}» уже списано {original-remaining:.1f} м. "
                "Сначала отмените более поздние производственные операции."
            )
        conn.execute(
            "UPDATE material_cost_layers SET remaining_meters=0,status='reversed',updated_at=? WHERE id=?",
            (now, int(layer["id"])),
        )

def read_material_cost_layers(active_only: bool = False) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        sql = "SELECT * FROM material_cost_layers"
        if active_only:
            sql += " WHERE status='active' AND remaining_meters>0.0005"
        sql += " ORDER BY material_name,COALESCE(source_date,'1900-01-01'),id"
        return pd.read_sql_query(sql, conn)

def read_material_fifo_summary() -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            WITH layers AS (
                SELECT material_key,
                       SUM(CASE WHEN status='active' THEN remaining_meters ELSE 0 END) AS fifo_meters,
                       SUM(CASE WHEN status='active' THEN remaining_meters*unit_cost_rub_m ELSE 0 END) AS fifo_amount,
                       COUNT(CASE WHEN status='active' AND remaining_meters>0.0005 THEN 1 END) AS active_layers
                  FROM material_cost_layers GROUP BY material_key
            ), next_layer AS (
                SELECT l.material_key,l.unit_cost_rub_m,l.source_type,l.source_ref,
                       ROW_NUMBER() OVER(PARTITION BY l.material_key ORDER BY COALESCE(l.source_date,'1900-01-01'),l.id) AS rn
                  FROM material_cost_layers l
                 WHERE l.status='active' AND l.remaining_meters>0.0005
            )
            SELECT i.material_key,i.material_name,i.balance_known,i.full_rolls,i.partial_meters,i.roll_length,
                   (COALESCE(i.full_rolls,0)*COALESCE(i.roll_length,25.5)+COALESCE(i.partial_meters,0)) AS physical_meters,
                   COALESCE(l.fifo_meters,0) AS fifo_meters,
                   COALESCE(l.fifo_amount,0) AS fifo_amount_rub,
                   CASE WHEN COALESCE(l.fifo_meters,0)>0 THEN l.fifo_amount/l.fifo_meters ELSE 0 END AS weighted_rate_rub_m,
                   COALESCE(n.unit_cost_rub_m,0) AS next_fifo_rate_rub_m,
                   COALESCE(n.source_type,'') AS next_source_type,
                   COALESCE(n.source_ref,'') AS next_source_ref,
                   COALESCE(l.active_layers,0) AS active_layers,
                   (COALESCE(i.full_rolls,0)*COALESCE(i.roll_length,25.5)+COALESCE(i.partial_meters,0))-COALESCE(l.fifo_meters,0) AS difference_meters
              FROM material_inventory_color i
              LEFT JOIN layers l ON l.material_key=i.material_key
              LEFT JOIN next_layer n ON n.material_key=i.material_key AND n.rn=1
             ORDER BY i.material_name
            """,
            conn,
        )

def read_production_cost_batches(limit: int = 500) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            "SELECT * FROM production_cost_batches ORDER BY id DESC LIMIT ?",
            conn, params=(max(1, int(limit)),),
        )

def read_production_cost_profiles() -> pd.DataFrame:
    """Weighted actual unit cost by article from completed, non-reversed production batches."""
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            SELECT b.nm_id,
                   MAX(b.supplier_article) AS supplier_article,
                   SUM(CASE WHEN b.status='active' THEN b.produced_units ELSE 0 END) AS produced_units,
                   SUM(CASE WHEN b.status='active' THEN b.total_cost_rub ELSE 0 END) AS actual_total_cost_rub,
                   CASE WHEN SUM(CASE WHEN b.status='active' THEN b.produced_units ELSE 0 END)>0
                        THEN SUM(CASE WHEN b.status='active' THEN b.total_cost_rub ELSE 0 END)
                             /SUM(CASE WHEN b.status='active' THEN b.produced_units ELSE 0 END)
                        ELSE 0 END AS actual_unit_cost_rub,
                   MAX(CASE WHEN b.status='active' THEN b.batch_date END) AS latest_batch_date,
                   COUNT(CASE WHEN b.status='active' THEN 1 END) AS active_batches
              FROM production_cost_batches b
             GROUP BY b.nm_id
            HAVING SUM(CASE WHEN b.status='active' THEN b.produced_units ELSE 0 END)>0
             ORDER BY supplier_article
            """,
            conn,
        )

def read_material_cost_rates() -> pd.DataFrame:
    """Latest full landed cost per metre for each material plus a global fallback.

    Supplier price, delivery and extras are recombined dynamically so an older
    stored ``unit_price`` cannot silently exclude freight.
    """
    from .core import connect
    with connect() as conn:
        frame = pd.read_sql_query(
            """
            SELECT i.material_key,i.material_name,i.roll_length,
                   CASE WHEN COALESCE(i.supplier_unit_price,0)>0
                              OR COALESCE(i.delivery_unit_foreign,0)>0
                              OR COALESCE(i.extra_unit_rub,0)>0
                        THEN (COALESCE(i.supplier_unit_price,0)+COALESCE(i.delivery_unit_foreign,0))
                             *CASE WHEN COALESCE(i.exchange_rate,0)>0 THEN i.exchange_rate ELSE 1 END
                             +COALESCE(i.extra_unit_rub,0)
                        ELSE COALESCE(i.unit_price,0) END AS unit_price,
                   CASE WHEN COALESCE(i.roll_length,0)>0 THEN
                        (CASE WHEN COALESCE(i.supplier_unit_price,0)>0
                                   OR COALESCE(i.delivery_unit_foreign,0)>0
                                   OR COALESCE(i.extra_unit_rub,0)>0
                              THEN (COALESCE(i.supplier_unit_price,0)+COALESCE(i.delivery_unit_foreign,0))
                                   *CASE WHEN COALESCE(i.exchange_rate,0)>0 THEN i.exchange_rate ELSE 1 END
                                   +COALESCE(i.extra_unit_rub,0)
                              ELSE COALESCE(i.unit_price,0) END)/i.roll_length
                        ELSE 0 END AS cost_per_meter_rub,
                   i.posted_quantity,o.id AS order_id,o.order_number,o.status,o.order_date,o.expected_date,
                   o.currency,o.exchange_rate,o.updated_at
              FROM procurement_items i
              JOIN procurement_orders o ON o.id=i.order_id
             WHERE i.item_type='Сырьё' AND COALESCE(i.roll_length,0)>0
               AND (COALESCE(i.unit_price,0)>0 OR COALESCE(i.supplier_unit_price,0)>0
                    OR COALESCE(i.delivery_unit_foreign,0)>0 OR COALESCE(i.extra_unit_rub,0)>0)
               AND o.status<>'Отменено'
             ORDER BY COALESCE(o.order_date,'') DESC,o.id DESC,i.id DESC
            """,
            conn,
        )
    if frame.empty:
        return frame
    frame["priority"] = (pd.to_numeric(frame["posted_quantity"], errors="coerce").fillna(0) > 0).astype(int)
    # Latest order is intentionally preferred so a new agreed price updates forecasts before receipt.
    frame = frame.sort_values(["order_date", "order_id", "priority"], ascending=[False, False, False])
    return frame.reset_index(drop=True)

def _material_rate_maps(conn: sqlite3.Connection) -> tuple[dict[str, float], float, dict[str, str], str]:
    rows = conn.execute(
        """
        SELECT i.material_key,i.roll_length,o.order_date,o.id,o.order_number,
               CASE WHEN COALESCE(i.supplier_unit_price,0)>0
                          OR COALESCE(i.delivery_unit_foreign,0)>0
                          OR COALESCE(i.extra_unit_rub,0)>0
                    THEN (COALESCE(i.supplier_unit_price,0)+COALESCE(i.delivery_unit_foreign,0))
                         *CASE WHEN COALESCE(i.exchange_rate,0)>0 THEN i.exchange_rate ELSE 1 END
                         +COALESCE(i.extra_unit_rub,0)
                    ELSE COALESCE(i.unit_price,0) END AS landed_unit_price
          FROM procurement_items i JOIN procurement_orders o ON o.id=i.order_id
         WHERE i.item_type='Сырьё' AND COALESCE(i.roll_length,0)>0
           AND (COALESCE(i.unit_price,0)>0 OR COALESCE(i.supplier_unit_price,0)>0
                OR COALESCE(i.delivery_unit_foreign,0)>0 OR COALESCE(i.extra_unit_rub,0)>0)
           AND o.status<>'Отменено'
         ORDER BY COALESCE(o.order_date,'') DESC,o.id DESC,i.id DESC
        """
    ).fetchall()
    exact: dict[str, float] = {}
    exact_source: dict[str, str] = {}
    fallback = 0.0
    fallback_source = ""
    for row in rows:
        rate = float(row["landed_unit_price"] or 0) / max(0.000001, float(row["roll_length"] or 0))
        key = str(row["material_key"] or "").strip().casefold()
        source = str(row["order_number"] or "")
        if fallback <= 0 and rate > 0:
            fallback = rate
            fallback_source = source
        if key and key not in exact and rate > 0:
            exact[key] = rate
            exact_source[key] = source
    return exact, fallback, exact_source, fallback_source

def refresh_auto_costs() -> int:
    """Refresh forecast costs without changing the cost currently used in profit.

    v4.2 deliberately separates the historical/current cost from the next-batch
    forecast. A forecast is applied only through ``apply_forecast_costs``.
    """
    from .core import connect
    now = datetime.now().isoformat(timespec="seconds")
    updated = 0
    with connect() as conn:
        exact, fallback, exact_source, fallback_source = _material_rate_maps(conn)
        rows = conn.execute(
            """
            SELECT p.nm_id,p.material_name,p.material_per_unit,
                   COALESCE(c.packaging_cost_rub,0) AS packaging_cost_rub,
                   COALESCE(c.labor_cost_rub,0) AS labor_cost_rub,
                   COALESCE(c.other_cost_rub,0) AS other_cost_rub
              FROM production_settings p
              LEFT JOIN costs c ON c.nm_id=p.nm_id
             WHERE COALESCE(p.enabled,0)=1
            """
        ).fetchall()
        for row in rows:
            key = str(row["material_name"] or "").strip().casefold()
            rate = exact.get(key, fallback)
            if rate <= 0:
                continue
            material_cost = max(0.0, float(row["material_per_unit"] or 0)) * rate
            packaging = max(0.0, float(row["packaging_cost_rub"] or 0))
            if packaging <= 0:
                packaging = 12.0
            total = material_cost + packaging + max(0.0, float(row["labor_cost_rub"] or 0)) + max(0.0, float(row["other_cost_rub"] or 0))
            source = exact_source.get(key, fallback_source)
            source_text = ("Цена цвета" if key in exact else "Общая ставка ПВХ") + (f" · {source}" if source else "")
            existing = conn.execute("SELECT nm_id FROM costs WHERE nm_id=?", (int(row["nm_id"]),)).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO costs(
                        nm_id,cost_per_wb_unit,auto_material_cost,material_cost_rub,packaging_cost_rub,
                        labor_cost_rub,other_cost_rub,forecast_material_cost_rub,forecast_total_cost_rub,
                        forecast_rate_rub_m,forecast_source,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (int(row["nm_id"]), 0.0, 0, 0.0, packaging,
                     max(0.0, float(row["labor_cost_rub"] or 0)), max(0.0, float(row["other_cost_rub"] or 0)),
                     material_cost, total, rate, source_text, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE costs
                       SET forecast_material_cost_rub=?,forecast_total_cost_rub=?,forecast_rate_rub_m=?,
                           forecast_source=?,packaging_cost_rub=CASE WHEN COALESCE(packaging_cost_rub,0)<=0 THEN ? ELSE packaging_cost_rub END,
                           updated_at=?
                     WHERE nm_id=?
                    """,
                    (material_cost, total, rate, source_text, packaging, now, int(row["nm_id"])),
                )
            updated += 1
    return updated

def save_auto_cost_settings(df: pd.DataFrame) -> None:
    from .core import _int, connect
    required = {"nm_id", "packaging_cost_rub", "labor_cost_rub", "other_cost_rub"}
    if df is None or df.empty or not required.issubset(df.columns):
        raise ValueError("Не хватает данных прогноза себестоимости.")
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        for _, row in df.iterrows():
            nm_id = _int(row.get("nm_id"))
            if nm_id <= 0:
                continue
            existing = conn.execute("SELECT * FROM costs WHERE nm_id=?", (nm_id,)).fetchone()
            supplier_article = str(row.get("supplier_article", existing["supplier_article"] if existing else "") or "")
            current_total = float(existing["cost_per_wb_unit"] or 0) if existing else 0.0
            packaging = max(0.0, float(row.get("packaging_cost_rub", 0) or 0))
            conn.execute(
                """
                INSERT INTO costs(
                    nm_id,supplier_article,cost_per_wb_unit,auto_material_cost,material_cost_rub,
                    packaging_cost_rub,labor_cost_rub,other_cost_rub,note,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(nm_id) DO UPDATE SET
                    supplier_article=excluded.supplier_article,
                    auto_material_cost=0,
                    packaging_cost_rub=excluded.packaging_cost_rub,
                    labor_cost_rub=excluded.labor_cost_rub,
                    other_cost_rub=excluded.other_cost_rub,
                    updated_at=excluded.updated_at
                """,
                (nm_id, supplier_article, current_total, 0,
                 float(existing["material_cost_rub"] or 0) if existing else 0.0,
                 packaging,
                 max(0.0, float(row.get("labor_cost_rub", 0) or 0)),
                 max(0.0, float(row.get("other_cost_rub", 0) or 0)),
                 str(row.get("note", existing["note"] if existing else "") or ""), now),
            )
    refresh_auto_costs()

def apply_forecast_costs(df: pd.DataFrame) -> int:
    """Apply selected next-batch forecasts to the cost used in profit."""
    from .core import _int, connect
    if df is None or df.empty or not {"nm_id", "apply_forecast"}.issubset(df.columns):
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    applied = 0
    with connect() as conn:
        for _, row in df.iterrows():
            if not bool(row.get("apply_forecast")):
                continue
            nm_id = _int(row.get("nm_id"))
            if nm_id <= 0:
                continue
            current = conn.execute("SELECT * FROM costs WHERE nm_id=?", (nm_id,)).fetchone()
            if current is None:
                continue
            forecast_total = max(0.0, float(current["forecast_total_cost_rub"] or 0))
            forecast_material = max(0.0, float(current["forecast_material_cost_rub"] or 0))
            if forecast_total <= 0:
                continue
            note = str(current["note"] or "").strip()
            addition = f"Применена себестоимость новой партии {datetime.now().date().isoformat()}"
            note = f"{note}; {addition}" if note else addition
            conn.execute(
                """
                UPDATE costs
                   SET cost_per_wb_unit=?,material_cost_rub=?,auto_material_cost=0,note=?,updated_at=?
                 WHERE nm_id=?
                """,
                (forecast_total, forecast_material, note, now, nm_id),
            )
            applied += 1
    return applied

def save_costs(df: pd.DataFrame) -> None:
    from .core import connect
    required = {"nm_id", "cost_per_wb_unit"}
    if not required.issubset(df.columns):
        raise ValueError("Нужны колонки nm_id и cost_per_wb_unit")
    rows = []
    for _, r in df.iterrows():
        if pd.isna(r.get("nm_id")):
            continue
        rows.append(
            (
                int(r["nm_id"]),
                str(r.get("supplier_article", "") or ""),
                float(r.get("cost_per_wb_unit", 0) or 0),
                str(r.get("note", "") or ""),
                datetime.now().isoformat(timespec="seconds"),
            )
        )
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO costs(nm_id,supplier_article,cost_per_wb_unit,auto_material_cost,note,updated_at)
            VALUES (?,?,?,0,?,?)
            ON CONFLICT(nm_id) DO UPDATE SET
                supplier_article=excluded.supplier_article,
                cost_per_wb_unit=excluded.cost_per_wb_unit,
                auto_material_cost=0,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            rows,
        )
