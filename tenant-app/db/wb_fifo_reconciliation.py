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


WB_INCIDENT_WAREHOUSE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Тула / Алексин", ("тула", "алексин")),
    ("Екатеринбург — Перспективная", ("екатеринбург - перспективная", "екатеринбург — перспективная")),
    ("Коледино", ("коледино",)),
    ("Новосемейкино", ("новосемейкино",)),
    ("Владимир WB", ("владимир wb",)),
)

FIFO_SYNC_GUARD_META_KEY = "finished_goods_fifo_sync_guard_v55"


def _wb_fifo_reconciliation_context_with_conn(conn: sqlite3.Connection) -> dict[str, Any]:
    """Diagnostic context for WB stock/FIFO reconciliation.

    WB stock snapshots can temporarily lose warehouse granularity and be returned as
    one aggregate warehouse (warehouse_id=-999999). In that mode `quantity` alone is
    not a safe basis for a FIFO write-off because units may also be inWayToClient / 
    inWayFromClient and external losses are not represented by ordinary sale events.
    """
    latest_row = conn.execute(
        "SELECT MAX(snapshot_at) AS snapshot_at FROM stocks"
    ).fetchone()
    latest_snapshot = str(latest_row["snapshot_at"] or "") if latest_row else ""
    current = {
        "available_units": 0, "in_way_to_client_units": 0, "in_way_from_client_units": 0,
        "wb_contour_units": 0, "warehouse_rows": 0, "warehouse_ids": 0,
        "aggregate_rows": 0,
    }
    if latest_snapshot:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(quantity),0) AS available_units,
                   COALESCE(SUM(in_way_to_client),0) AS in_way_to_client_units,
                   COALESCE(SUM(in_way_from_client),0) AS in_way_from_client_units,
                   COUNT(*) AS warehouse_rows,
                   COUNT(DISTINCT warehouse_id) AS warehouse_ids,
                   SUM(CASE WHEN warehouse_id=-999999 THEN 1 ELSE 0 END) AS aggregate_rows
              FROM stocks WHERE snapshot_at=?
            """, (latest_snapshot,),
        ).fetchone()
        if row:
            current.update({k: int(row[k] or 0) for k in current if k != "wb_contour_units"})
            current["wb_contour_units"] = (
                current["available_units"] + current["in_way_to_client_units"] + current["in_way_from_client_units"]
            )

    fifo_row = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN status<>'reversed' THEN wb_units ELSE 0 END),0) AS units,
               COALESCE(SUM(CASE WHEN status<>'reversed' THEN wb_units*unit_cost_rub ELSE 0 END),0) AS amount
          FROM finished_goods_cost_layers
        """
    ).fetchone()
    fifo_units = int(fifo_row["units"] or 0) if fifo_row else 0
    fifo_amount = float(fifo_row["amount"] or 0) if fifo_row else 0.0

    aggregated = bool(
        latest_snapshot
        and current["warehouse_rows"] > 0
        and current["aggregate_rows"] == current["warehouse_rows"]
    )

    detailed_row = conn.execute(
        """
        SELECT MAX(snapshot_at) AS snapshot_at
          FROM stocks
         WHERE warehouse_id<>-999999
           AND (?='' OR snapshot_at<?)
        """, (latest_snapshot, latest_snapshot),
    ).fetchone()
    detailed_snapshot = str(detailed_row["snapshot_at"] or "") if detailed_row else ""
    detailed_available = detailed_to = detailed_from = detailed_contour = 0
    incident_available = incident_contour = 0
    incident_rows: list[dict[str, Any]] = []
    if detailed_snapshot:
        drow = conn.execute(
            """
            SELECT COALESCE(SUM(quantity),0) AS available_units,
                   COALESCE(SUM(in_way_to_client),0) AS in_way_to_client_units,
                   COALESCE(SUM(in_way_from_client),0) AS in_way_from_client_units
              FROM stocks WHERE snapshot_at=?
            """, (detailed_snapshot,),
        ).fetchone()
        if drow:
            detailed_available = int(drow["available_units"] or 0)
            detailed_to = int(drow["in_way_to_client_units"] or 0)
            detailed_from = int(drow["in_way_from_client_units"] or 0)
            detailed_contour = detailed_available + detailed_to + detailed_from

        wh_rows = conn.execute(
            """
            SELECT warehouse_name,
                   COALESCE(SUM(quantity),0) AS available_units,
                   COALESCE(SUM(in_way_to_client),0) AS in_way_to_client_units,
                   COALESCE(SUM(in_way_from_client),0) AS in_way_from_client_units
              FROM stocks WHERE snapshot_at=? GROUP BY warehouse_name
            """, (detailed_snapshot,),
        ).fetchall()
        for label, patterns in WB_INCIDENT_WAREHOUSE_PATTERNS:
            available = to_client = from_client = 0
            matched_names: list[str] = []
            for wh in wh_rows:
                wh_name = str(wh["warehouse_name"] or "")
                lowered = wh_name.lower()
                if any(pattern in lowered for pattern in patterns):
                    available += int(wh["available_units"] or 0)
                    to_client += int(wh["in_way_to_client_units"] or 0)
                    from_client += int(wh["in_way_from_client_units"] or 0)
                    matched_names.append(wh_name)
            if matched_names:
                contour = available + to_client + from_client
                incident_available += available
                incident_contour += contour
                incident_rows.append({
                    "warehouse": label,
                    "wb_names": ", ".join(sorted(set(matched_names))),
                    "available_units": available,
                    "in_way_to_client_units": to_client,
                    "in_way_from_client_units": from_client,
                    "contour_units": contour,
                })

    sale_units = return_units = 0
    if detailed_snapshot:
        srow = conn.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN is_return=0 THEN 1 ELSE 0 END),0) AS sale_units,
                   COALESCE(SUM(CASE WHEN is_return=1 THEN 1 ELSE 0 END),0) AS return_units
              FROM sales
             WHERE datetime(sale_date) > datetime(?)
            """, (detailed_snapshot,),
        ).fetchone()
        if srow:
            sale_units = int(srow["sale_units"] or 0)
            return_units = int(srow["return_units"] or 0)
    net_sales = sale_units - return_units

    manual_receipts_since_detailed = 0
    confirmed_fbw_receipts_since_detailed = 0
    if detailed_snapshot:
        mrow = conn.execute(
            """
            SELECT COALESCE(SUM(quantity),0) AS units
              FROM inventory_movements
             WHERE movement_type='wb_receipt' AND status='applied' AND reversed_at IS NULL
               AND datetime(COALESCE(movement_date,created_at)) > datetime(?)
            """, (detailed_snapshot,),
        ).fetchone()
        manual_receipts_since_detailed = int(mrow["units"] or 0) if mrow else 0
        try:
            frow = conn.execute(
                """
                SELECT COALESCE(SUM(g.quantity),0) AS units
                  FROM wb_fbw_supply_goods g
                  JOIN wb_fbw_supply_confirmations s ON s.supply_id=g.supply_id
                 WHERE s.accepted=1 AND s.status_id=5 AND s.fact_date_msk>?
                """, (detailed_snapshot,),
            ).fetchone()
            confirmed_fbw_receipts_since_detailed = int(frow["units"] or 0) if frow else 0
        except sqlite3.OperationalError:
            confirmed_fbw_receipts_since_detailed = 0

    registered_receipts_since_detailed = manual_receipts_since_detailed + confirmed_fbw_receipts_since_detailed
    expected_without_external = (
        max(0, detailed_contour + registered_receipts_since_detailed - net_sales) if detailed_snapshot else 0
    )
    diagnostic_gap = max(0, expected_without_external - current["wb_contour_units"]) if detailed_snapshot else 0

    return {
        "latest_snapshot_at": latest_snapshot,
        "aggregated_snapshot": aggregated,
        "warehouse_detail_available": bool(latest_snapshot and not aggregated),
        **current,
        "fifo_wb_units": fifo_units,
        "fifo_wb_amount_rub": round(fifo_amount, 2),
        "wb_contour_minus_fifo": current["wb_contour_units"] - fifo_units,
        "last_detailed_snapshot_at": detailed_snapshot,
        "last_detailed_available_units": detailed_available,
        "last_detailed_in_way_to_client_units": detailed_to,
        "last_detailed_in_way_from_client_units": detailed_from,
        "last_detailed_contour_units": detailed_contour,
        "sales_since_detailed": sale_units,
        "returns_since_detailed": return_units,
        "net_sales_since_detailed": net_sales,
        "manual_wb_receipts_since_detailed": manual_receipts_since_detailed,
        "confirmed_fbw_receipts_since_detailed": confirmed_fbw_receipts_since_detailed,
        "registered_wb_receipts_since_detailed": registered_receipts_since_detailed,
        "expected_contour_without_external_events": expected_without_external,
        "diagnostic_external_gap_units": diagnostic_gap,
        "incident_available_units": incident_available,
        "incident_contour_units": incident_contour,
        "incident_rows": incident_rows,
    }

def wb_fifo_reconciliation_context() -> dict[str, Any]:
    from .core import connect
    with connect() as conn:
        return _wb_fifo_reconciliation_context_with_conn(conn)

def read_wb_incident_exposure() -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        ctx = _wb_fifo_reconciliation_context_with_conn(conn)
    return pd.DataFrame(ctx.get("incident_rows", []))

def _wb_incident_label(warehouse_name: str) -> str:
    lowered = str(warehouse_name or "").strip().lower()
    for label, patterns in WB_INCIDENT_WAREHOUSE_PATTERNS:
        if any(pattern in lowered for pattern in patterns):
            return label
    return ""

def _read_wb_incident_reconciliation_with_conn(conn: sqlite3.Connection) -> pd.DataFrame:
    """Per-SKU diagnostic model for possible external WB warehouse losses.

    The model starts from the last detailed stock snapshot, adds only explicitly
    registered WB receipts after that snapshot, subtracts API sales and adds API
    returns, and compares the expected contour with the latest WB contour. It is a
    diagnostic model, not proof of loss: undocumented supplies/transfers and changes
    in WB stock classification can still explain part of the gap.
    """
    ctx = _wb_fifo_reconciliation_context_with_conn(conn)
    baseline_at = str(ctx.get("last_detailed_snapshot_at", "") or "")
    current_at = str(ctx.get("latest_snapshot_at", "") or "")
    if not baseline_at or not current_at:
        return pd.DataFrame()

    baseline = pd.read_sql_query(
        """
        SELECT s.nm_id,
               COALESCE(SUM(s.quantity),0) AS baseline_available,
               COALESCE(SUM(s.in_way_to_client),0) AS baseline_to_client,
               COALESCE(SUM(s.in_way_from_client),0) AS baseline_from_client
          FROM stocks s
         WHERE s.snapshot_at=?
         GROUP BY s.nm_id
        """, conn, params=(baseline_at,),
    )
    current = pd.read_sql_query(
        """
        SELECT s.nm_id,
               COALESCE(SUM(s.quantity),0) AS current_available,
               COALESCE(SUM(s.in_way_to_client),0) AS current_to_client,
               COALESCE(SUM(s.in_way_from_client),0) AS current_from_client
          FROM stocks s
         WHERE s.snapshot_at=?
         GROUP BY s.nm_id
        """, conn, params=(current_at,),
    )
    sales = pd.read_sql_query(
        """
        SELECT nm_id,
               COALESCE(SUM(CASE WHEN is_return=0 THEN 1 ELSE 0 END),0) AS sales_units,
               COALESCE(SUM(CASE WHEN is_return=1 THEN 1 ELSE 0 END),0) AS return_units
          FROM sales
         WHERE datetime(sale_date) > datetime(?)
         GROUP BY nm_id
        """, conn, params=(baseline_at,),
    )
    manual_receipts = pd.read_sql_query(
        """
        SELECT nm_id,COALESCE(SUM(quantity),0) AS manual_wb_receipts
          FROM inventory_movements
         WHERE movement_type='wb_receipt' AND status='applied' AND reversed_at IS NULL
           AND datetime(COALESCE(movement_date,created_at)) > datetime(?)
         GROUP BY nm_id
        """, conn, params=(baseline_at,),
    )
    try:
        fbw_receipts = pd.read_sql_query(
            """
            SELECT g.nm_id,COALESCE(SUM(g.quantity),0) AS confirmed_fbw_receipts
              FROM wb_fbw_supply_goods g
              JOIN wb_fbw_supply_confirmations s ON s.supply_id=g.supply_id
             WHERE s.accepted=1 AND s.status_id=5 AND s.fact_date_msk>?
             GROUP BY g.nm_id
            """, conn, params=(baseline_at,),
        )
    except Exception:
        fbw_receipts = pd.DataFrame(columns=["nm_id","confirmed_fbw_receipts"])
    fifo = pd.read_sql_query(
        """
        SELECT nm_id,
               COALESCE(SUM(CASE WHEN status<>'reversed' THEN wb_units ELSE 0 END),0) AS fifo_wb_units,
               COALESCE(SUM(CASE WHEN status<>'reversed' THEN wb_units*unit_cost_rub ELSE 0 END),0) AS fifo_wb_amount
          FROM finished_goods_cost_layers GROUP BY nm_id
        """, conn,
    )
    catalog = pd.read_sql_query(
        """
        SELECT pc.nm_id,COALESCE(pc.supplier_article,c.supplier_article,'') AS supplier_article,
               COALESCE(pc.product_name,'') AS product_name,
               COALESCE(c.cost_per_wb_unit,0) AS baseline_rate
          FROM products_catalog pc LEFT JOIN costs c ON c.nm_id=pc.nm_id
        UNION
        SELECT c.nm_id,COALESCE(c.supplier_article,''),'',COALESCE(c.cost_per_wb_unit,0)
          FROM costs c WHERE c.nm_id NOT IN (SELECT nm_id FROM products_catalog)
        """, conn,
    )
    confirmed = pd.read_sql_query(
        """
        SELECT nm_id,COALESCE(SUM(confirmed_units),0) AS confirmed_loss_units,
               COALESCE(SUM(fifo_cost_rub),0) AS confirmed_loss_cost_rub
          FROM wb_incident_loss_lines WHERE status='applied' GROUP BY nm_id
        """, conn,
    )

    incident_rows = conn.execute(
        """
        SELECT nm_id,warehouse_name,
               COALESCE(quantity,0) AS quantity,
               COALESCE(in_way_to_client,0) AS in_way_to_client,
               COALESCE(in_way_from_client,0) AS in_way_from_client
          FROM stocks WHERE snapshot_at=?
        """, (baseline_at,),
    ).fetchall()
    exposure_map: dict[int, dict[str, Any]] = {}
    for row in incident_rows:
        label = _wb_incident_label(str(row["warehouse_name"] or ""))
        if not label:
            continue
        nm_id = int(row["nm_id"] or 0)
        item = exposure_map.setdefault(nm_id, {
            "incident_available": 0, "incident_to_client": 0, "incident_from_client": 0,
            "incident_exposure_units": 0, "incident_warehouses": set(),
        })
        available = int(row["quantity"] or 0)
        to_client = int(row["in_way_to_client"] or 0)
        from_client = int(row["in_way_from_client"] or 0)
        item["incident_available"] += available
        item["incident_to_client"] += to_client
        item["incident_from_client"] += from_client
        item["incident_exposure_units"] += available + to_client + from_client
        item["incident_warehouses"].add(label)
    exposure = pd.DataFrame([
        {"nm_id": nm_id, **{k: (", ".join(sorted(v)) if isinstance(v, set) else v) for k, v in values.items()}}
        for nm_id, values in exposure_map.items()
    ])

    frames = [frame for frame in (baseline,current,sales,manual_receipts,fbw_receipts,fifo,catalog,confirmed,exposure) if not frame.empty]
    if not frames:
        return pd.DataFrame()
    ids = sorted(set().union(*[set(pd.to_numeric(f["nm_id"], errors="coerce").fillna(0).astype(int).tolist()) for f in frames]))
    out = pd.DataFrame({"nm_id": [x for x in ids if x > 0]})
    for frame in frames:
        frame = frame.copy()
        frame["nm_id"] = pd.to_numeric(frame["nm_id"], errors="coerce").fillna(0).astype(int)
        out = out.merge(frame, on="nm_id", how="left")

    numeric_cols = [
        "baseline_available","baseline_to_client","baseline_from_client",
        "current_available","current_to_client","current_from_client",
        "sales_units","return_units","manual_wb_receipts","confirmed_fbw_receipts","registered_wb_receipts","fifo_wb_units","fifo_wb_amount",
        "baseline_rate","confirmed_loss_units","confirmed_loss_cost_rub",
        "incident_available","incident_to_client","incident_from_client","incident_exposure_units",
    ]
    for col in numeric_cols:
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    for col in ("supplier_article","product_name","incident_warehouses"):
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str)

    for col in [c for c in numeric_cols if c not in {"fifo_wb_amount","baseline_rate","confirmed_loss_cost_rub"}]:
        out[col] = out[col].round().astype(int)
    out["baseline_contour"] = out["baseline_available"] + out["baseline_to_client"] + out["baseline_from_client"]
    out["current_contour"] = out["current_available"] + out["current_to_client"] + out["current_from_client"]
    out["net_sales_units"] = out["sales_units"] - out["return_units"]
    out["registered_wb_receipts"] = (out["manual_wb_receipts"] + out["confirmed_fbw_receipts"]).astype(int)
    out["expected_without_external"] = (
        out["baseline_contour"] + out["registered_wb_receipts"] - out["net_sales_units"]
    ).clip(lower=0).astype(int)
    out["external_gap_units"] = (out["expected_without_external"] - out["current_contour"]).clip(lower=0).astype(int)
    out["unexplained_increase_units"] = (out["current_contour"] - out["expected_without_external"]).clip(lower=0).astype(int)
    out["incident_candidate_raw_units"] = out[["external_gap_units","incident_exposure_units"]].min(axis=1).clip(lower=0).astype(int)

    # Per-SKU gaps are not additive because some SKUs clearly received unregistered replenishment
    # after the detailed snapshot. Allocate only the *global* external-gap benchmark across
    # incident-exposed SKUs, proportionally to their raw signal. This keeps the SKU view
    # mathematically tied to the 800-unit (or future) global diagnostic benchmark.
    # v6.3: once accepted FBW supplies are verified and stored, the global
    # historical balance must include them. The context helper now does so too,
    # therefore the benchmark can rise (e.g. 800 -> 1200) when real replenishment
    # happened after the baseline but before the current snapshot.
    global_gap = max(0, int(ctx.get("diagnostic_external_gap_units", 0) or 0))
    out["global_external_gap_units"] = global_gap
    raw_total = int(out["incident_candidate_raw_units"].sum())
    target = min(global_gap, raw_total)
    allocated = pd.Series(0, index=out.index, dtype="int64")
    if target > 0 and raw_total > 0:
        exact = out["incident_candidate_raw_units"].astype(float) * (float(target) / float(raw_total))
        allocated = exact.apply(math.floor).astype(int)
        remainder = int(target - allocated.sum())
        if remainder > 0:
            fractions = (exact - allocated).sort_values(ascending=False)
            for idx in fractions.index[:remainder]:
                allocated.loc[idx] += 1
    out["incident_candidate_units"] = allocated.astype(int)
    out["unconfirmed_candidate_units"] = (
        out["incident_candidate_units"] - out["confirmed_loss_units"]
    ).clip(lower=0).astype(int)
    out["fifo_gap_units"] = out["fifo_wb_units"] - out["current_contour"]

    # v6.1: safe incident posting guard.  A documented warehouse loss may only
    # consume the FIFO surplus that exists above the *current full WB contour*
    # (available + inWayToClient + inWayFromClient).  This prevents an incident
    # write-off from making FIFO smaller than the physical WB contour.
    out["safe_fifo_capacity_units"] = out["fifo_gap_units"].clip(lower=0).astype(int)
    out["safe_post_now_units"] = out[["unconfirmed_candidate_units", "safe_fifo_capacity_units"]].min(axis=1).clip(lower=0).astype(int)
    out["candidate_blocked_by_layers_units"] = (
        out["unconfirmed_candidate_units"] - out["safe_post_now_units"]
    ).clip(lower=0).astype(int)
    out["current_layer_shortfall_units"] = (-out["fifo_gap_units"]).clip(lower=0).astype(int)
    out["layer_restoration_needed_units"] = (
        out["current_layer_shortfall_units"] + out["candidate_blocked_by_layers_units"]
    ).astype(int)
    # v6.2: conservation-law lower bound for incoming stock after the baseline.
    # Inventory balance per SKU is:
    #   current = baseline + registered_inflow + unregistered_inflow
    #             - net_sales - confirmed_external_loss.
    # Therefore the minimum unregistered inflow compatible with the observed
    # current contour is the positive part of the reverse balance.  This is a
    # stronger and mathematically correct lower bound than merely looking at a
    # positive stock delta when net sales already exceeded the baseline stock.
    # It is diagnostic only and never creates a cost layer automatically.
    out["inferred_unregistered_inflow_floor_units"] = (
        out["current_contour"]
        + out["net_sales_units"]
        + out["confirmed_loss_units"]
        - out["baseline_contour"]
        - out["registered_wb_receipts"]
    ).clip(lower=0).astype(int)

    out["fifo_avg_rate"] = out.apply(
        lambda r: (float(r["fifo_wb_amount"] or 0) / int(r["fifo_wb_units"])) if int(r["fifo_wb_units"] or 0) > 0
        else float(r["baseline_rate"] or 0), axis=1,
    )
    out["incident_candidate_cost_rub"] = (out["incident_candidate_units"] * out["fifo_avg_rate"]).round(2)
    out["safe_post_now_cost_rub"] = (out["safe_post_now_units"] * out["fifo_avg_rate"]).round(2)

    def status(row: pd.Series) -> str:
        gap = int(row["external_gap_units"] or 0)
        exposure_units = int(row["incident_exposure_units"] or 0)
        increase = int(row["unexplained_increase_units"] or 0)
        layer_shortfall = int(row.get("current_layer_shortfall_units", 0) or 0)
        blocked = int(row.get("candidate_blocked_by_layers_units", 0) or 0)
        if layer_shortfall > 0 and gap <= 0:
            return "Контур выше FIFO — восстановить слои"
        if gap <= 0:
            return "Рост/без разрыва" if increase > 0 else "Нет сигнала"
        if blocked > 0:
            return "Кандидат частично заблокирован слоями"
        if exposure_units <= 0:
            return "Разрыв вне экспозиции"
        if exposure_units >= gap:
            return "Совместимо с инцидентом"
        return "Частично совместимо"
    out["diagnostic_status"] = out.apply(status, axis=1)
    out["baseline_snapshot_at"] = baseline_at
    out["current_snapshot_at"] = current_at
    out = out.sort_values(
        ["unconfirmed_candidate_units","external_gap_units","incident_exposure_units","supplier_article"],
        ascending=[False,False,False,True],
    ).reset_index(drop=True)
    return out

def read_wb_incident_reconciliation() -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return _read_wb_incident_reconciliation_with_conn(conn)

def _fifo_reconciliation_preview_with_conn(conn: sqlite3.Connection) -> pd.DataFrame:
    """Return diagnostic physical-vs-layer differences for ready, inbound and WB balances.

    v5.9 safety rule: WB rows are diagnostic only. They are never automatically
    reconciled because WB `quantity` is not the full stock contour and external
    warehouse losses/compensations are not ordinary sale events. For WB we compare
    FIFO layers against quantity + inWayToClient + inWayFromClient only as a signal.
    """
    context = _wb_fifo_reconciliation_context_with_conn(conn)
    summary = pd.read_sql_query(
        """
        WITH latest_stock AS (SELECT MAX(snapshot_at) AS snapshot_at FROM stocks),
        wb AS (
            SELECT s.nm_id,
                   SUM(COALESCE(s.quantity,0)) AS wb_available,
                   SUM(COALESCE(s.in_way_to_client,0)) AS wb_to_client,
                   SUM(COALESCE(s.in_way_from_client,0)) AS wb_from_client,
                   SUM(COALESCE(s.quantity,0)+COALESCE(s.in_way_to_client,0)+COALESCE(s.in_way_from_client,0)) AS wb_contour
              FROM stocks s JOIN latest_stock l ON s.snapshot_at=l.snapshot_at GROUP BY s.nm_id
        ), goods AS (
            SELECT nm_id,
                   SUM(CASE WHEN status<>'reversed' THEN ready_units ELSE 0 END) AS ready_layer_units,
                   SUM(CASE WHEN status<>'reversed' THEN ready_units*unit_cost_rub ELSE 0 END) AS ready_amount,
                   SUM(CASE WHEN status<>'reversed' THEN inbound_units ELSE 0 END) AS inbound_layer_units,
                   SUM(CASE WHEN status<>'reversed' THEN inbound_units*unit_cost_rub ELSE 0 END) AS inbound_amount,
                   SUM(CASE WHEN status<>'reversed' THEN wb_units ELSE 0 END) AS wb_layer_units,
                   SUM(CASE WHEN status<>'reversed' THEN wb_units*unit_cost_rub ELSE 0 END) AS wb_amount
              FROM finished_goods_cost_layers GROUP BY nm_id
        ), ids AS (
            SELECT nm_id FROM products_catalog UNION SELECT nm_id FROM product_pipeline
            UNION SELECT nm_id FROM wb UNION SELECT nm_id FROM goods UNION SELECT nm_id FROM costs
        )
        SELECT i.nm_id,COALESCE(pc.supplier_article,c.supplier_article,'') AS supplier_article,
               COALESCE(pc.product_name,'') AS product_name,
               COALESCE(pp.local_known,0) AS ready_known,COALESCE(pp.ready_units,0) AS ready_physical,
               COALESCE(g.ready_layer_units,0) AS ready_layered,
               CASE WHEN COALESCE(g.ready_layer_units,0)>0 THEN g.ready_amount/g.ready_layer_units ELSE COALESCE(c.cost_per_wb_unit,0) END AS ready_rate,
               COALESCE(pp.inbound_known,0) AS inbound_known,COALESCE(pp.inbound_units,0) AS inbound_physical,
               COALESCE(g.inbound_layer_units,0) AS inbound_layered,
               CASE WHEN COALESCE(g.inbound_layer_units,0)>0 THEN g.inbound_amount/g.inbound_layer_units ELSE COALESCE(c.cost_per_wb_unit,0) END AS inbound_rate,
               CASE WHEN (SELECT snapshot_at FROM latest_stock) IS NOT NULL THEN 1 ELSE 0 END AS wb_known,
               COALESCE(w.wb_available,0) AS wb_available,
               COALESCE(w.wb_to_client,0) AS wb_to_client,
               COALESCE(w.wb_from_client,0) AS wb_from_client,
               COALESCE(w.wb_contour,0) AS wb_physical,
               COALESCE(g.wb_layer_units,0) AS wb_layered,
               CASE WHEN COALESCE(g.wb_layer_units,0)>0 THEN g.wb_amount/g.wb_layer_units ELSE COALESCE(c.cost_per_wb_unit,0) END AS wb_rate,
               COALESCE(c.cost_per_wb_unit,0) AS baseline_rate
          FROM ids i
          LEFT JOIN products_catalog pc ON pc.nm_id=i.nm_id
          LEFT JOIN costs c ON c.nm_id=i.nm_id
          LEFT JOIN product_pipeline pp ON pp.nm_id=i.nm_id
          LEFT JOIN wb w ON w.nm_id=i.nm_id
          LEFT JOIN goods g ON g.nm_id=i.nm_id
         WHERE i.nm_id>0
        """, conn,
    )
    rows: list[dict[str, Any]] = []
    labels = {"ready": "Готово у вас", "inbound": "В пути", "wb": "Контур WB"}
    for _, row in summary.iterrows():
        nm_id = int(row.get("nm_id", 0) or 0)
        for location in ("ready", "inbound", "wb"):
            if int(row.get(f"{location}_known", 0) or 0) != 1:
                continue
            physical = max(0, int(row.get(f"{location}_physical", 0) or 0))
            layered = max(0, int(row.get(f"{location}_layered", 0) or 0))
            diff = physical - layered
            if diff == 0:
                continue
            rate = max(0.0, float(row.get(f"{location}_rate", 0) or row.get("baseline_rate", 0) or 0))
            is_wb = location == "wb"
            if is_wb:
                safe_to_reconcile = 0
                if diff < 0:
                    action = "Не списывать — возможная утрата/статус WB"
                    risk_status = "Проверка внешнего выбытия"
                else:
                    action = "Не досоздавать — проверить данные WB"
                    risk_status = "Проверка данных WB"
                basis = "Доступно + к клиенту + от клиента"
            else:
                safe_to_reconcile = 1
                action = "Досоздать слой" if diff > 0 else "Списать из слоёв"
                risk_status = "Обычная сверка"
                basis = "Подтверждённый управленческий остаток"
            rows.append({
                "nm_id": nm_id,
                "supplier_article": str(row.get("supplier_article", "") or ""),
                "product_name": str(row.get("product_name", "") or ""),
                "location": location,
                "location_name": labels[location],
                "physical_units": physical,
                "layered_units": layered,
                "difference_units": diff,
                "action": action,
                "unit_cost_rub": round(rate, 4),
                "amount_rub": round(abs(diff) * rate, 2),
                "safe_to_reconcile": safe_to_reconcile,
                "risk_status": risk_status,
                "physical_basis": basis,
                "wb_available_units": int(row.get("wb_available", 0) or 0) if is_wb else 0,
                "wb_in_way_to_client_units": int(row.get("wb_to_client", 0) or 0) if is_wb else 0,
                "wb_in_way_from_client_units": int(row.get("wb_from_client", 0) or 0) if is_wb else 0,
                "aggregated_wb_snapshot": int(bool(context.get("aggregated_snapshot"))) if is_wb else 0,
            })
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(["safe_to_reconcile", "location", "difference_units", "supplier_article"], ascending=[True, True, True, True]).reset_index(drop=True)
    return result

def preview_finished_goods_reconciliation() -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return _fifo_reconciliation_preview_with_conn(conn)

def apply_finished_goods_reconciliation(note: str = "") -> dict[str, Any]:
    """Apply only safe local reconciliation rows.

    v5.9 intentionally blocks all WB-location adjustments. WB discrepancies may be
    caused by units in transit, aggregated stock snapshots, warehouse incidents or
    later compensation flows. They must be resolved through a dedicated external-
    loss workflow, not by consuming FIFO layers as if stock simply disappeared.
    """
    from .core import connect
    from .fifo_finished_goods import _create_finished_goods_layer, _move_finished_goods_fifo
    from .production import _next_event_key
    result: dict[str, Any] = {
        "run_id": 0, "lines": 0, "articles": 0, "added_units": 0, "removed_units": 0,
        "amount_rub": 0.0, "skipped_lines": 0, "skipped_units": 0,
    }
    with connect() as conn:
        try:
            preview = _fifo_reconciliation_preview_with_conn(conn)
            if preview.empty:
                return result
            safe_preview = preview[pd.to_numeric(preview.get("safe_to_reconcile", 0), errors="coerce").fillna(0).astype(int).eq(1)].copy()
            blocked_preview = preview[pd.to_numeric(preview.get("safe_to_reconcile", 0), errors="coerce").fillna(0).astype(int).ne(1)].copy()
            result["skipped_lines"] = int(len(blocked_preview))
            result["skipped_units"] = int(pd.to_numeric(blocked_preview.get("difference_units", 0), errors="coerce").fillna(0).abs().sum()) if not blocked_preview.empty else 0
            if safe_preview.empty:
                return result

            latest = conn.execute("SELECT MAX(snapshot_at) AS snapshot_at FROM stocks").fetchone()
            snapshot_at = str(latest["snapshot_at"] or "") if latest else ""
            now = datetime.now()
            run_key = f"fifo_reconcile:{now.strftime('%Y%m%d%H%M%S%f')}"
            cur = conn.execute(
                """
                INSERT INTO fifo_reconciliation_runs(
                    run_key,snapshot_at,run_date,status,articles,lines,added_units,removed_units,
                    adjustment_amount_rub,note,created_at
                ) VALUES (?,?,?,'applying',0,0,0,0,0,?,?)
                """,
                (run_key, snapshot_at, now.date().isoformat(), str(note or ""), now.isoformat(timespec="seconds")),
            )
            run_id = int(cur.lastrowid)
            article_ids: set[int] = set()
            added = removed = 0
            amount_total = 0.0
            for _, item in safe_preview.iterrows():
                nm_id = int(item["nm_id"])
                article = str(item["supplier_article"] or "")
                product = str(item["product_name"] or "")
                location = str(item["location"] or "wb")
                physical = int(item["physical_units"])
                layered = int(item["layered_units"])
                diff = int(item["difference_units"])
                rate = max(0.0, float(item["unit_cost_rub"] or 0))
                movement_id: int | None = None
                layer_id: int | None = None
                line_note = ""
                amount = round(abs(diff) * rate, 2)
                if diff > 0:
                    layer_id = _create_finished_goods_layer(
                        conn, nm_id, article, product, f"reconciliation_increase_{location}",
                        f"reconciliation:{run_id}:{location}:{nm_id}", now.date().isoformat(), diff, rate, location,
                        "Досоздано по подтверждённому локальному остатку при управленческой сверке FIFO.",
                    )
                    added += diff
                    line_note = "Создан слой по подтверждённому локальному остатку; физическое количество не менялось."
                else:
                    qty = abs(diff)
                    source_key = f"fifo_reconcile:{run_id}:{location}:{nm_id}"
                    move_cur = conn.execute(
                        """
                        INSERT INTO inventory_movements(
                            event_key,movement_type,nm_id,supplier_article,product_name,quantity,
                            ready_delta,inbound_delta,route,movement_date,expected_arrival_date,
                            source_task_key,reference_movement_id,status,note,created_at,reversed_at,
                            goods_cost_rub,goods_unit_cost_rub
                        ) VALUES (?,?,?,?,?,?,0,0,?,?,NULL,?,NULL,'applied',?,?,NULL,0,0)
                        """,
                        (
                            _next_event_key(conn, "fifo_cost_reconciliation", source_key),
                            "fifo_cost_reconciliation", nm_id, article, product, qty,
                            f"FIFO reconciliation: {location}", now.date().isoformat(), source_key,
                            "Локальная стоимостная сверка; не является продажей и не относится к остатку WB.",
                            now.isoformat(timespec="seconds"),
                        ),
                    )
                    movement_id = int(move_cur.lastrowid)
                    cost, _ = _move_finished_goods_fifo(conn, movement_id, nm_id, article, product, qty, location, None)
                    amount = round(cost, 2)
                    conn.execute(
                        "UPDATE inventory_movements SET goods_cost_rub=?,goods_unit_cost_rub=? WHERE id=?",
                        (amount, round(amount / qty, 4) if qty else 0.0, movement_id),
                    )
                    removed += qty
                    line_note = "Списано из локальных FIFO-слоёв; COGS продаж не затронут."
                conn.execute(
                    """
                    INSERT INTO fifo_reconciliation_lines(
                        run_id,nm_id,supplier_article,product_name,location,physical_units,layered_units,
                        difference_units,action,unit_cost_rub,amount_rub,movement_id,layer_id,status,note,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'applied',?,?)
                    """,
                    (
                        run_id,nm_id,article,product,location,physical,layered,diff,str(item["action"]),rate,amount,
                        movement_id,layer_id,line_note,now.isoformat(timespec="seconds"),
                    ),
                )
                article_ids.add(nm_id)
                amount_total += amount
            conn.execute(
                """
                UPDATE fifo_reconciliation_runs
                   SET status='applied',articles=?,lines=?,added_units=?,removed_units=?,adjustment_amount_rub=?
                 WHERE id=?
                """,
                (len(article_ids), len(safe_preview), added, removed, round(amount_total, 2), run_id),
            )
            result.update({
                "run_id": run_id, "lines": len(safe_preview), "articles": len(article_ids),
                "added_units": added, "removed_units": removed, "amount_rub": round(amount_total, 2),
            })
            return result
        except Exception:
            conn.rollback()
            raise

def fifo_reconciliation_status() -> dict[str, Any]:
    from .core import connect
    with connect() as conn:
        latest = conn.execute(
            "SELECT * FROM fifo_reconciliation_runs WHERE status='applied' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        counts = conn.execute(
            """
            SELECT COUNT(*) AS runs,COALESCE(SUM(lines),0) AS lines,
                   COALESCE(SUM(added_units),0) AS added_units,COALESCE(SUM(removed_units),0) AS removed_units,
                   COALESCE(SUM(adjustment_amount_rub),0) AS amount_rub
              FROM fifo_reconciliation_runs WHERE status='applied'
            """
        ).fetchone()
        preview = _fifo_reconciliation_preview_with_conn(conn)
        if preview.empty:
            current_added = current_removed = current_blocked = safe_added = safe_removed = 0
        else:
            diffs = pd.to_numeric(preview["difference_units"], errors="coerce").fillna(0)
            safe = pd.to_numeric(preview.get("safe_to_reconcile", 0), errors="coerce").fillna(0).astype(int).eq(1)
            current_added = int(diffs[diffs > 0].sum())
            current_removed = int((-diffs[diffs < 0]).sum())
            current_blocked = int(diffs[~safe].abs().sum())
            safe_added = int(diffs[safe & (diffs > 0)].sum())
            safe_removed = int((-diffs[safe & (diffs < 0)]).sum())
        ctx = _wb_fifo_reconciliation_context_with_conn(conn)
        return {
            "runs": int(counts["runs"] or 0),
            "lines": int(counts["lines"] or 0),
            "added_units": int(counts["added_units"] or 0),
            "removed_units": int(counts["removed_units"] or 0),
            "amount_rub": float(counts["amount_rub"] or 0),
            "current_lines": len(preview),
            "current_articles": int(preview["nm_id"].nunique()) if not preview.empty else 0,
            "current_added": current_added,
            "current_removed": current_removed,
            "current_blocked_units": current_blocked,
            "current_safe_added": safe_added,
            "current_safe_removed": safe_removed,
            "last_run_id": int(latest["id"] or 0) if latest else 0,
            "last_run_at": str(latest["created_at"] or "") if latest else "",
            "last_snapshot_at": str(latest["snapshot_at"] or "") if latest else "",
            "aggregated_wb_snapshot": bool(ctx.get("aggregated_snapshot")),
            "wb_contour_units": int(ctx.get("wb_contour_units", 0) or 0),
            "fifo_wb_units": int(ctx.get("fifo_wb_units", 0) or 0),
            "diagnostic_external_gap_units": int(ctx.get("diagnostic_external_gap_units", 0) or 0),
        }

def _parse_guard_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        py_dt = parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed
        if getattr(py_dt, "tzinfo", None) is not None:
            py_dt = py_dt.replace(tzinfo=None)
        return py_dt
    except Exception:
        return None

def finished_goods_fifo_guard_status(
    sync_finished_at: str = "",
    *,
    persistent_minutes: int = 60,
    persistent_cycles: int = 2,
    small_limit_units: int = 50,
) -> dict[str, Any]:
    """Classify FIFO drift and protect WB layers from unsafe automatic write-offs."""
    from .core import connect
    now = datetime.now()
    with connect() as conn:
        preview = _fifo_reconciliation_preview_with_conn(conn)
        ctx = _wb_fifo_reconciliation_context_with_conn(conn)
        total_units = int(pd.to_numeric(preview.get("difference_units", 0), errors="coerce").fillna(0).abs().sum()) if not preview.empty else 0
        articles = int(preview["nm_id"].nunique()) if not preview.empty else 0
        added_units = int(pd.to_numeric(preview.loc[preview["difference_units"] > 0, "difference_units"], errors="coerce").fillna(0).sum()) if not preview.empty else 0
        removed_units = int((-pd.to_numeric(preview.loc[preview["difference_units"] < 0, "difference_units"], errors="coerce").fillna(0)).sum()) if not preview.empty else 0
        if preview.empty:
            blocked_units = safe_units = 0
        else:
            safe_mask = pd.to_numeric(preview.get("safe_to_reconcile", 0), errors="coerce").fillna(0).astype(int).eq(1)
            blocked_units = int(pd.to_numeric(preview.loc[~safe_mask, "difference_units"], errors="coerce").fillna(0).abs().sum())
            safe_units = int(pd.to_numeric(preview.loc[safe_mask, "difference_units"], errors="coerce").fillna(0).abs().sum())

        stock_snapshot_at = str(ctx.get("latest_snapshot_at", "") or "")
        event_row = conn.execute(
            "SELECT MAX(updated_at) AS updated_at,MAX(event_date) AS event_date FROM sales_fifo_events WHERE status='applied'"
        ).fetchone()
        last_fifo_event_at = str((event_row["updated_at"] or event_row["event_date"] or "") if event_row else "")
        sync_finished_at = str(sync_finished_at or "")

        pipeline_row = conn.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN local_known=1 THEN ready_units ELSE 0 END),0)
                 + COALESCE(SUM(CASE WHEN inbound_known=1 THEN inbound_units ELSE 0 END),0) AS units
              FROM product_pipeline
            """
        ).fetchone()
        pipeline_units = int(pipeline_row["units"] or 0) if pipeline_row else 0
        physical_units = int(ctx.get("wb_contour_units", 0) or 0) + pipeline_units
        large_threshold = max(100, int(round(max(1, physical_units) * 0.05)))

        state_row = conn.execute("SELECT value FROM app_meta WHERE key=?", (FIFO_SYNC_GUARD_META_KEY,)).fetchone()
        try:
            previous = json.loads(str(state_row["value"] or "{}")) if state_row else {}
        except Exception:
            previous = {}

        observation_key = "|".join([
            stock_snapshot_at, sync_finished_at, last_fifo_event_at,
            str(articles), str(added_units), str(removed_units), str(blocked_units),
            str(int(bool(ctx.get("aggregated_snapshot")))),
        ])

        if total_units <= 0:
            first_seen_at = ""
            cycles = 0
            age_minutes = 0.0
        else:
            prev_total = int(previous.get("total_units", 0) or 0)
            prev_key = str(previous.get("last_observation_key", "") or "")
            if prev_total > 0:
                first_seen_at = str(previous.get("first_seen_at", "") or now.isoformat(timespec="seconds"))
                cycles = max(1, int(previous.get("cycles", 1) or 1))
                if observation_key != prev_key:
                    cycles += 1
            else:
                first_seen_at = now.isoformat(timespec="seconds")
                cycles = 1
            first_seen_dt = _parse_guard_datetime(first_seen_at) or now
            age_minutes = max(0.0, (now - first_seen_dt).total_seconds() / 60.0)

        aggregated = bool(ctx.get("aggregated_snapshot"))
        wb_review_required = blocked_units > 0
        is_large = total_units >= large_threshold if total_units > 0 else False
        is_persistent = (
            total_units > 0
            and cycles >= max(2, int(persistent_cycles))
            and age_minutes >= max(1, int(persistent_minutes))
        )

        if total_units <= 0:
            status = "Готово"
            guard_mode = "ok"
            reason = "Физические остатки и FIFO-слои совпадают."
        elif wb_review_required:
            status = "Внимание"
            guard_mode = "wb_incident_review"
            if aggregated:
                reason = (
                    "WB отдаёт агрегированный снимок склада без детализации по объектам. "
                    f"В контуре WB сейчас {int(ctx.get('wb_contour_units',0) or 0)} ед. "
                    f"({int(ctx.get('available_units',0) or 0)} доступно + "
                    f"{int(ctx.get('in_way_to_client_units',0) or 0)} к клиенту + "
                    f"{int(ctx.get('in_way_from_client_units',0) or 0)} от клиента), "
                    f"в FIFO-слоях WB {int(ctx.get('fifo_wb_units',0) or 0)} ед. "
                    "Автоматическая управленческая сверка WB заблокирована: расхождение может включать складские утраты/пожары и переходные статусы."
                )
            else:
                reason = (
                    f"По WB есть диагностическое расхождение {blocked_units} ед. Оно не считается обычной недостачей FIFO. "
                    "Списание/досоздание WB-слоёв заблокировано до подтверждения внешней утраты или другого события WB."
                )
        else:
            guard_mode = "local_reconciliation"
            if is_large:
                status = "Критично"
                reason = f"Крупное локальное расхождение: {total_units} ед. при пороге {large_threshold} ед."
            elif is_persistent:
                status = "Критично"
                reason = f"Локальное расхождение сохраняется {cycles} контрольных цикла и {age_minutes:.0f} мин."
            else:
                status = "Ожидание API"
                reason = (
                    f"Небольшое недавнее локальное расхождение: {total_units} ед.; "
                    f"цикл {cycles}, возраст {age_minutes:.0f} мин."
                )

        state = {
            "status": status,
            "guard_mode": guard_mode,
            "reason": reason,
            "first_seen_at": first_seen_at,
            "last_seen_at": now.isoformat(timespec="seconds"),
            "last_observation_key": observation_key,
            "cycles": cycles,
            "total_units": total_units,
            "articles": articles,
            "added_units": added_units,
            "removed_units": removed_units,
            "blocked_wb_units": blocked_units,
            "safe_reconciliation_units": safe_units,
            "stock_snapshot_at": stock_snapshot_at,
            "last_fifo_event_at": last_fifo_event_at,
            "sync_finished_at": sync_finished_at,
            "physical_units": physical_units,
            "large_threshold": large_threshold,
            "age_minutes": round(age_minutes, 1),
            "persistent_minutes": int(persistent_minutes),
            "persistent_cycles": int(persistent_cycles),
            "small_limit_units": int(small_limit_units),
            "is_large": bool(is_large),
            "is_persistent": bool(is_persistent),
            "aggregated_wb_snapshot": aggregated,
            "wb_contour_units": int(ctx.get("wb_contour_units", 0) or 0),
            "wb_available_units": int(ctx.get("available_units", 0) or 0),
            "wb_in_way_to_client_units": int(ctx.get("in_way_to_client_units", 0) or 0),
            "wb_in_way_from_client_units": int(ctx.get("in_way_from_client_units", 0) or 0),
            "fifo_wb_units": int(ctx.get("fifo_wb_units", 0) or 0),
            "last_detailed_snapshot_at": str(ctx.get("last_detailed_snapshot_at", "") or ""),
            "diagnostic_external_gap_units": int(ctx.get("diagnostic_external_gap_units", 0) or 0),
            "incident_available_units": int(ctx.get("incident_available_units", 0) or 0),
            "incident_contour_units": int(ctx.get("incident_contour_units", 0) or 0),
        }
        conn.execute(
            """
            INSERT INTO app_meta(key,value,updated_at) VALUES (?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
            """,
            (FIFO_SYNC_GUARD_META_KEY, json.dumps(state, ensure_ascii=False), now.isoformat(timespec="seconds")),
        )

        # WB rows are never reconciled by the generic management button in v5.9.
        state["manual_reconciliation_allowed"] = bool(status == "Критично" and blocked_units == 0 and safe_units > 0)
        state["wait_minutes"] = max(0, int(persistent_minutes - age_minutes))
        return state

def read_fifo_reconciliation_runs(limit: int = 100) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            "SELECT * FROM fifo_reconciliation_runs ORDER BY id DESC LIMIT ?",
            conn, params=(max(1, int(limit)),),
        )

def read_fifo_reconciliation_lines(run_id: int | None = None, limit: int = 500) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        if run_id:
            return pd.read_sql_query(
                "SELECT * FROM fifo_reconciliation_lines WHERE run_id=? ORDER BY id",
                conn, params=(int(run_id),),
            )
        return pd.read_sql_query(
            "SELECT * FROM fifo_reconciliation_lines ORDER BY id DESC LIMIT ?",
            conn, params=(max(1, int(limit)),),
        )

