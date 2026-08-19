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


from .wb_fifo_reconciliation import (
    _wb_fifo_reconciliation_context_with_conn,
    _read_wb_incident_reconciliation_with_conn,
)
from .wb_fbw_evidence import _wb_msk_naive_iso


def _resolve_reconstruction_cost_basis_with_conn(
    conn: sqlite3.Connection, nm_id: int, as_of: Any,
) -> dict[str, Any]:
    """Best available historical unit-cost evidence for a reconstructed FIFO layer.

    Priority is deliberately conservative:
    1) completed production cost batch existing on/before the reconstructed date;
    2) non-synthetic historical FIFO layer rate existing on/before that date;
    3) historical/opening FIFO layer rate;
    4) configured baseline product cost;
    5) forecast cost;
    6) current weighted FIFO rate.

    The function is read-only.  It never claims a baseline/forecast rate is an actual
    production cost: source_code/confidence are returned so the UI can distinguish them.
    """
    nm_id = int(nm_id or 0)
    as_of_iso = _wb_msk_naive_iso(as_of) or str(as_of or "")
    as_of_day = str(as_of_iso)[:10]
    if nm_id <= 0:
        return {
            "rate": 0.0, "source_code": "missing", "source_label": "Нет ставки",
            "confidence": "Нет", "evidence_date": "", "detail": "nmID не определён",
        }

    # Highest quality: an actually closed production cost batch available at that date.
    actual = conn.execute(
        """
        SELECT batch_date,
               SUM(produced_units) AS units,
               SUM(total_cost_rub) AS amount
          FROM production_cost_batches
         WHERE nm_id=? AND status='active' AND produced_units>0 AND total_cost_rub>0
           AND date(COALESCE(batch_date,created_at))<=date(?)
         GROUP BY date(COALESCE(batch_date,created_at))
         ORDER BY date(COALESCE(batch_date,created_at)) DESC
         LIMIT 1
        """,
        (nm_id, as_of_day),
    ).fetchone()
    if actual and int(actual["units"] or 0) > 0:
        units = int(actual["units"] or 0)
        amount = float(actual["amount"] or 0)
        rate = amount / units if units else 0.0
        if rate > 0:
            return {
                "rate": rate,
                "source_code": "actual_production",
                "source_label": "Факт производства",
                "confidence": "Высокая",
                "evidence_date": str(actual["batch_date"] or "")[:10],
                "detail": f"Последняя закрытая производственная партия до {as_of_day}: {units} ед.",
            }

    # Historical FIFO rates that existed before the target date. Reconciliation layers are
    # retained here because they preserve the rate that the system actually used at that time,
    # but they are not promoted to 'actual production'.
    hist = conn.execute(
        """
        SELECT date(COALESCE(source_date,created_at)) AS evidence_date,
               SUM(original_units) AS units,
               SUM(original_units*unit_cost_rub) AS amount
          FROM finished_goods_cost_layers
         WHERE nm_id=? AND status<>'reversed' AND original_units>0 AND unit_cost_rub>0
           AND datetime(COALESCE(source_date,created_at))<=datetime(?)
           AND source_type NOT IN ('opening_wb','synthetic_sale','return_unmatched')
         GROUP BY date(COALESCE(source_date,created_at))
         ORDER BY date(COALESCE(source_date,created_at)) DESC
         LIMIT 1
        """,
        (nm_id, as_of_iso),
    ).fetchone()
    if hist and int(hist["units"] or 0) > 0:
        units = int(hist["units"] or 0)
        amount = float(hist["amount"] or 0)
        rate = amount / units if units else 0.0
        if rate > 0:
            return {
                "rate": rate,
                "source_code": "historical_fifo",
                "source_label": "Исторический FIFO",
                "confidence": "Средняя",
                "evidence_date": str(hist["evidence_date"] or ""),
                "detail": f"Ставка из несинтетических FIFO-слоёв, существовавших к {as_of_day}",
            }

    opening = conn.execute(
        """
        SELECT date(COALESCE(source_date,created_at)) AS evidence_date,
               SUM(original_units) AS units,
               SUM(original_units*unit_cost_rub) AS amount
          FROM finished_goods_cost_layers
         WHERE nm_id=? AND status<>'reversed' AND original_units>0 AND unit_cost_rub>0
           AND datetime(COALESCE(source_date,created_at))<=datetime(?)
           AND source_type IN ('opening_wb','return_unmatched')
         GROUP BY date(COALESCE(source_date,created_at))
         ORDER BY date(COALESCE(source_date,created_at)) DESC
         LIMIT 1
        """,
        (nm_id, as_of_iso),
    ).fetchone()
    if opening and int(opening["units"] or 0) > 0:
        units = int(opening["units"] or 0)
        amount = float(opening["amount"] or 0)
        rate = amount / units if units else 0.0
        if rate > 0:
            return {
                "rate": rate,
                "source_code": "historical_opening_fifo",
                "source_label": "Исторический opening FIFO",
                "confidence": "Средняя−",
                "evidence_date": str(opening["evidence_date"] or ""),
                "detail": f"Историческая opening/return ставка, не факт производственной партии",
            }

    cost = conn.execute(
        """
        SELECT cost_per_wb_unit,forecast_total_cost_rub,updated_at,COALESCE(note,'') AS note
          FROM costs WHERE nm_id=?
        """,
        (nm_id,),
    ).fetchone()
    if cost:
        base = max(0.0, float(cost["cost_per_wb_unit"] or 0))
        if base > 0:
            note = str(cost["note"] or "")
            historical = "истор" in note.casefold()
            return {
                "rate": base,
                "source_code": "baseline_cost",
                "source_label": "Историческая базовая ставка" if historical else "Базовая ставка",
                "confidence": "Средняя−" if historical else "Низкая",
                "evidence_date": str(cost["updated_at"] or "")[:10],
                "detail": note or "Настроенная себестоимость товара; не факт конкретной партии",
            }
        forecast = max(0.0, float(cost["forecast_total_cost_rub"] or 0))
        if forecast > 0:
            return {
                "rate": forecast,
                "source_code": "forecast_cost",
                "source_label": "Прогноз себестоимости",
                "confidence": "Низкая",
                "evidence_date": str(cost["updated_at"] or "")[:10],
                "detail": "Текущая прогнозная себестоимость; используется только как fallback",
            }

    weighted = conn.execute(
        """
        SELECT SUM(wb_units) AS units,SUM(wb_units*unit_cost_rub) AS amount
          FROM finished_goods_cost_layers
         WHERE nm_id=? AND status<>'reversed' AND wb_units>0 AND unit_cost_rub>0
        """,
        (nm_id,),
    ).fetchone()
    if weighted and int(weighted["units"] or 0) > 0:
        units = int(weighted["units"] or 0)
        amount = float(weighted["amount"] or 0)
        rate = amount / units if units else 0.0
        if rate > 0:
            return {
                "rate": rate,
                "source_code": "current_fifo_weighted",
                "source_label": "Текущий средний FIFO",
                "confidence": "Низкая",
                "evidence_date": "",
                "detail": "Fallback по текущим остаточным слоям, не исторический факт",
            }

    return {
        "rate": 0.0,
        "source_code": "missing",
        "source_label": "Нет ставки",
        "confidence": "Нет",
        "evidence_date": "",
        "detail": "Нет доступного источника себестоимости",
    }

def preview_retroactive_fbw_fifo_reconstruction(_return_plan: bool = False):
    """Preview a historical FIFO replay with verified FBW receipts inserted at factDate.

    v6.4 is deliberately read-only.  It reconstructs the WB FIFO position immediately
    before the earliest verified post-baseline supply by undoing already-posted sales/
    returns in memory, inserts accepted FBW supplies at their factual acceptance time,
    and replays the same sale/return stream chronologically.  No cost layer, allocation,
    sale event or inventory movement is changed in SQLite.

    Supply unit cost is provisional: the current configured product cost is used only to
    show the possible COGS impact.  Quantity/timing comes from the verified FBW API data.
    """
    from .core import connect
    with connect() as conn:
        ctx = _wb_fifo_reconciliation_context_with_conn(conn)
        baseline_at = str(ctx.get("last_detailed_snapshot_at", "") or "")
        current_at = str(ctx.get("latest_snapshot_at", "") or "")
        if not baseline_at or not current_at:
            return {"ready": False, "reason": "Нет детального и текущего снимка WB."}, pd.DataFrame()

        supplies = conn.execute(
            """
            SELECT supply_id,fact_date_msk,fact_date,status_id,warehouse_name,units
              FROM wb_fbw_supply_confirmations
             WHERE accepted=1 AND status_id=5 AND fact_date_msk>? AND units>0
             ORDER BY fact_date_msk,supply_id
            """,
            (baseline_at,),
        ).fetchall()
        if not supplies:
            return {"ready": False, "reason": "После базового снимка нет подтверждённых принятых FBW-поставок."}, pd.DataFrame()

        earliest_supply_at = min(str(row["fact_date_msk"] or "") for row in supplies if str(row["fact_date_msk"] or ""))
        if not earliest_supply_at:
            return {"ready": False, "reason": "У подтверждённых поставок отсутствует factDate."}, pd.DataFrame()
        # The replay anchor is the last trustworthy detailed WB snapshot, not the
        # first supply.  This is important because the legacy FIFO baseline used only
        # warehouse `quantity`, while a cost inventory must also retain units already
        # travelling to/from clients until a sale/return event closes them.
        replay_start = baseline_at

        supply_goods = conn.execute(
            """
            SELECT g.supply_id,g.nm_id,COALESCE(g.supplier_article,'') AS supplier_article,
                   g.quantity,s.fact_date_msk,s.warehouse_name
              FROM wb_fbw_supply_goods g
              JOIN wb_fbw_supply_confirmations s ON s.supply_id=g.supply_id
             WHERE s.accepted=1 AND s.status_id=5 AND s.fact_date_msk>? AND g.quantity>0
             ORDER BY s.fact_date_msk,g.supply_id,g.nm_id
            """,
            (baseline_at,),
        ).fetchall()
        if not supply_goods:
            return {"ready": False, "reason": "Подтверждённые поставки не содержат SKU/количеств."}, pd.DataFrame()

        # Product metadata and provisional cost rates.  These values are never written
        # to FIFO by the preview; they only allow the user to inspect the possible COGS.
        product_rows = conn.execute(
            """
            SELECT pc.nm_id,COALESCE(pc.supplier_article,c.supplier_article,'') AS supplier_article,
                   COALESCE(pc.product_name,'') AS product_name,COALESCE(c.cost_per_wb_unit,0) AS baseline_rate
              FROM products_catalog pc LEFT JOIN costs c ON c.nm_id=pc.nm_id
            UNION
            SELECT c.nm_id,COALESCE(c.supplier_article,''),'',COALESCE(c.cost_per_wb_unit,0)
              FROM costs c WHERE c.nm_id NOT IN (SELECT nm_id FROM products_catalog)
            """
        ).fetchall()
        meta: dict[int, dict[str, Any]] = {}
        for row in product_rows:
            nm_id = int(row["nm_id"] or 0)
            if nm_id <= 0:
                continue
            meta[nm_id] = {
                "supplier_article": str(row["supplier_article"] or ""),
                "product_name": str(row["product_name"] or ""),
                "baseline_rate": max(0.0, float(row["baseline_rate"] or 0)),
            }
        weighted_rows = conn.execute(
            """
            SELECT nm_id,COALESCE(SUM(wb_units*unit_cost_rub),0) AS amount,
                   COALESCE(SUM(wb_units),0) AS units
              FROM finished_goods_cost_layers
             WHERE status<>'reversed' AND wb_units>0 GROUP BY nm_id
            """
        ).fetchall()
        weighted_rate = {
            int(r["nm_id"]): (float(r["amount"] or 0) / int(r["units"]))
            for r in weighted_rows if int(r["units"] or 0) > 0
        }

        cost_basis_cache: dict[tuple[int, str], dict[str, Any]] = {}

        def cost_basis(nm_id: int, as_of: Any) -> dict[str, Any]:
            key = (int(nm_id), str(_wb_msk_naive_iso(as_of) or as_of or "")[:19])
            if key not in cost_basis_cache:
                cost_basis_cache[key] = _resolve_reconstruction_cost_basis_with_conn(conn, int(nm_id), as_of)
            return cost_basis_cache[key]

        def provisional_rate(nm_id: int, as_of: Any = None) -> float:
            basis = cost_basis(int(nm_id), as_of or baseline_at)
            rate = max(0.0, float(basis.get("rate", 0) or 0))
            if rate > 0:
                return rate
            base = float(meta.get(int(nm_id), {}).get("baseline_rate", 0) or 0)
            if base > 0:
                return base
            return max(0.0, float(weighted_rate.get(int(nm_id), 0) or 0))

        def local_ts(value: Any, fallback: str = "1900-01-01T00:00:00") -> pd.Timestamp:
            normalized = _wb_msk_naive_iso(value)
            ts = pd.to_datetime(normalized or value, errors="coerce")
            if pd.isna(ts):
                ts = pd.Timestamp(fallback)
            return pd.Timestamp(ts)

        replay_start_ts = local_ts(replay_start)

        # Recreate the WB layer state immediately before replay_start.  Current layer
        # balances already include all later sales/returns, so allocations posted after
        # replay_start are undone in memory.  Layers physically created after replay_start
        # (mainly old auto/synthetic stopgaps) did not exist at the historical start and
        # are intentionally excluded from the starting pool.
        all_layers = conn.execute(
            """
            SELECT * FROM finished_goods_cost_layers
             WHERE status<>'reversed'
             ORDER BY COALESCE(source_date,'1900-01-01'),id
            """
        ).fetchall()
        layers: dict[str, dict[str, Any]] = {}
        db_layer_key: dict[int, str] = {}
        ignored_auto_units = 0
        ignored_future_layers = 0
        ignored_future_units = 0
        for row in all_layers:
            created_ts = local_ts(row["created_at"] or row["source_date"] or "1900-01-01")
            source_type = str(row["source_type"] or "")
            original = max(0, int(row["original_units"] or 0))
            if created_ts >= replay_start_ts:
                ignored_future_layers += 1
                ignored_future_units += original
                if source_type in {"opening_wb", "synthetic_sale", "return_unmatched"}:
                    ignored_auto_units += original
                continue
            key = f"db:{int(row['id'])}"
            nm_id = int(row["nm_id"] or 0)
            layers[key] = {
                "key": key, "db_layer_id": int(row["id"]), "nm_id": nm_id,
                "source_ts": local_ts(row["source_date"] or row["created_at"]),
                "sequence": int(row["id"]), "remaining": max(0, int(row["wb_units"] or 0)),
                "original_units": original,
                "unit_cost": max(0.0, float(row["unit_cost_rub"] or 0)),
                "source_type": source_type, "source_ref": str(row["source_ref"] or ""),
                "provisional": False,
            }
            db_layer_key[int(row["id"])] = key

        post_allocations = conn.execute(
            """
            SELECT a.layer_id,a.units,a.from_location,a.to_location,e.id AS sale_event_id,e.event_date
              FROM finished_goods_fifo_allocations a
              JOIN sales_fifo_events e ON e.movement_id=a.movement_id
             WHERE a.status='applied' AND e.status='applied' AND datetime(e.event_date)>=datetime(?)
             ORDER BY datetime(e.event_date),e.id,a.id
            """,
            (replay_start,),
        ).fetchall()
        for a in post_allocations:
            key = db_layer_key.get(int(a["layer_id"] or 0))
            if not key or key not in layers:
                continue
            units = max(0, int(a["units"] or 0))
            from_loc = str(a["from_location"] or "")
            to_loc = str(a["to_location"] or "")
            if from_loc == "wb" and to_loc in {"out", "customer", ""}:
                layers[key]["remaining"] += units
            elif from_loc == "customer" and to_loc == "wb":
                layers[key]["remaining"] = max(0, int(layers[key]["remaining"]) - units)

        starting_units_before_bridge = sum(max(0, int(x["remaining"])) for x in layers.values())

        # Bridge the historical FIFO to the *full* physical WB contour at the detailed
        # snapshot (available + inWayToClient + inWayFromClient).  The old initializer
        # represented mostly `quantity`, which is why a large cost-layer deficit existed
        # even before the warehouse incidents.  This bridge is preview-only and uses an
        # estimated product cost; the quantity itself is anchored by the detailed API
        # snapshot.
        baseline_rows = conn.execute(
            """
            SELECT nm_id,COALESCE(SUM(quantity),0)+COALESCE(SUM(in_way_to_client),0)+COALESCE(SUM(in_way_from_client),0) AS contour
              FROM stocks WHERE snapshot_at=? GROUP BY nm_id
            """,
            (baseline_at,),
        ).fetchall()
        baseline_contour_map = {int(r["nm_id"]): max(0, int(r["contour"] or 0)) for r in baseline_rows}
        prebridge_map: dict[int, int] = {}
        for layer in layers.values():
            nm_id = int(layer["nm_id"]); prebridge_map[nm_id] = prebridge_map.get(nm_id, 0) + max(0, int(layer["remaining"]))
        bridge_by_nm: dict[int, int] = {}
        bridge_cost_by_nm: dict[int, float] = {}
        bridge_basis_by_nm: dict[int, dict[str, Any]] = {}
        bridge_sequence = 900_000
        for nm_id, contour_units in baseline_contour_map.items():
            missing = max(0, int(contour_units) - int(prebridge_map.get(nm_id, 0)))
            if missing <= 0:
                continue
            basis = cost_basis(nm_id, replay_start)
            rate = provisional_rate(nm_id, replay_start)
            bridge_basis_by_nm[nm_id] = dict(basis)
            key = f"sim:baseline_contour:{nm_id}:{bridge_sequence}"
            layers[key] = {
                "key": key, "db_layer_id": 0, "nm_id": int(nm_id), "source_ts": replay_start_ts,
                "sequence": bridge_sequence, "remaining": missing, "original_units": missing, "unit_cost": rate,
                "source_type": "baseline_contour_preview", "source_ref": f"snapshot:{baseline_at}:{nm_id}", "provisional": True,
            }
            bridge_by_nm[nm_id] = missing
            bridge_cost_by_nm[nm_id] = missing * rate
            bridge_sequence += 1
        baseline_bridge_units = int(sum(bridge_by_nm.values()))
        baseline_bridge_cost = float(sum(bridge_cost_by_nm.values()))
        starting_units = sum(max(0, int(x["remaining"])) for x in layers.values())

        # Current exact event stream.  We replay only events that were actually applied;
        # baseline history remains untouched because it predates the verified supplies.
        sale_events = conn.execute(
            """
            SELECT e.id,e.sale_record_id,e.nm_id,e.supplier_article,e.product_name,e.event_date,
                   e.event_type,e.fifo_cost_rub,e.unit_cost_rub,e.matched_sale_event_id,e.movement_id,e.srid
              FROM sales_fifo_events e
             WHERE e.status='applied' AND datetime(e.event_date)>=datetime(?)
             ORDER BY datetime(e.event_date),e.id
            """,
            (replay_start,),
        ).fetchall()

        # Manual WB receipts are already treated as factual incoming in the incident
        # balance.  Include them in the same read-only timeline if present.
        manual_receipts = conn.execute(
            """
            SELECT id,nm_id,COALESCE(supplier_article,'') AS supplier_article,
                   COALESCE(product_name,'') AS product_name,quantity,movement_date,created_at,
                   COALESCE(goods_unit_cost_rub,0) AS goods_unit_cost_rub
              FROM inventory_movements
             WHERE movement_type='wb_receipt' AND status='applied' AND reversed_at IS NULL
               AND datetime(COALESCE(movement_date,created_at))>=datetime(?) AND quantity>0
             ORDER BY datetime(COALESCE(movement_date,created_at)),id
            """,
            (replay_start,),
        ).fetchall()

        timeline: list[dict[str, Any]] = []
        sequence = 1_000_000
        supply_by_nm: dict[int, int] = {}
        supply_cost_by_nm: dict[int, float] = {}
        supply_basis_rows_by_nm: dict[int, list[dict[str, Any]]] = {}
        for g in supply_goods:
            nm_id = int(g["nm_id"] or 0)
            qty = max(0, int(g["quantity"] or 0))
            if nm_id <= 0 or qty <= 0:
                continue
            basis = cost_basis(nm_id, g["fact_date_msk"])
            rate = provisional_rate(nm_id, g["fact_date_msk"])
            supply_by_nm[nm_id] = supply_by_nm.get(nm_id, 0) + qty
            supply_cost_by_nm[nm_id] = supply_cost_by_nm.get(nm_id, 0.0) + qty * rate
            supply_basis_rows_by_nm.setdefault(nm_id, []).append({
                **dict(basis), "units": qty, "supply_id": int(g["supply_id"]),
                "fact_date": str(g["fact_date_msk"] or ""),
            })
            timeline.append({
                "ts": local_ts(g["fact_date_msk"]), "order": 0, "kind": "supply",
                "nm_id": nm_id, "units": qty, "unit_cost": rate,
                "supply_id": int(g["supply_id"]), "supplier_article": str(g["supplier_article"] or ""),
                "sequence": sequence,
            })
            sequence += 1
        for m in manual_receipts:
            nm_id = int(m["nm_id"] or 0); qty = max(0, int(m["quantity"] or 0))
            if nm_id <= 0 or qty <= 0:
                continue
            rate = max(0.0, float(m["goods_unit_cost_rub"] or 0)) or provisional_rate(nm_id, m["movement_date"] or m["created_at"])
            timeline.append({
                "ts": local_ts(m["movement_date"] or m["created_at"]), "order": 0, "kind": "manual_receipt",
                "nm_id": nm_id, "units": qty, "unit_cost": rate, "movement_id": int(m["id"]),
                "supplier_article": str(m["supplier_article"] or ""), "sequence": sequence,
            })
            sequence += 1
        for e in sale_events:
            timeline.append({
                "ts": local_ts(e["event_date"]), "order": 1, "kind": str(e["event_type"] or "sale"),
                "nm_id": int(e["nm_id"] or 0), "units": max(1, int(1)), "event_id": int(e["id"]),
                "matched_sale_event_id": int(e["matched_sale_event_id"] or 0),
                "movement_id": int(e["movement_id"] or 0),
                "current_cost": max(0.0, float(e["fifo_cost_rub"] or 0)),
                "supplier_article": str(e["supplier_article"] or ""), "sequence": int(e["id"]),
            })
        timeline.sort(key=lambda x: (x["ts"], int(x["order"]), int(x["sequence"])))

        # Existing allocations of matched sales before replay_start are needed when a
        # post-start return refers to an older exact FIFO sale.
        pre_match_allocs: dict[int, list[tuple[str, int, float]]] = {}
        matched_ids = sorted({int(x.get("matched_sale_event_id", 0)) for x in timeline if x["kind"] == "return" and int(x.get("matched_sale_event_id", 0)) > 0})
        if matched_ids:
            placeholders = ",".join("?" for _ in matched_ids)
            old_allocs = conn.execute(
                f"""
                SELECT e.id AS sale_event_id,a.layer_id,a.units,a.amount_rub
                  FROM sales_fifo_events e
                  JOIN finished_goods_fifo_allocations a ON a.movement_id=e.movement_id
                 WHERE e.id IN ({placeholders}) AND a.status='applied' AND a.from_location='wb'
                 ORDER BY e.id,a.id
                """,
                tuple(matched_ids),
            ).fetchall()
            for a in old_allocs:
                key = db_layer_key.get(int(a["layer_id"] or 0))
                if not key or key not in layers:
                    continue
                units = max(0, int(a["units"] or 0))
                if units <= 0:
                    continue
                rate = float(a["amount_rub"] or 0) / units if units else 0.0
                pre_match_allocs.setdefault(int(a["sale_event_id"]), []).append((key, units, rate))

        # Simulation helpers.
        new_sequence = 10_000_000
        sale_allocations: dict[int, list[tuple[str, int, float]]] = {}
        return_allocations: dict[int, list[tuple[str, int, float]]] = {}
        per_nm: dict[int, dict[str, Any]] = {}

        def bucket(nm_id: int) -> dict[str, Any]:
            b = per_nm.setdefault(int(nm_id), {
                "replay_sales": 0, "replay_returns": 0, "current_cogs": 0.0, "preview_cogs": 0.0,
                "synthetic_units": 0, "supply_units": int(supply_by_nm.get(int(nm_id), 0)),
                "supply_provisional_cost": float(supply_cost_by_nm.get(int(nm_id), 0.0)),
                "baseline_bridge_units": int(bridge_by_nm.get(int(nm_id), 0)),
                "baseline_bridge_cost": float(bridge_cost_by_nm.get(int(nm_id), 0.0)),
            })
            return b

        def add_layer(nm_id: int, units: int, rate: float, ts: pd.Timestamp, source_type: str, source_ref: str, provisional: bool) -> str:
            nonlocal new_sequence
            key = f"sim:{source_type}:{source_ref}:{new_sequence}"
            layers[key] = {
                "key": key, "db_layer_id": 0, "nm_id": int(nm_id), "source_ts": pd.Timestamp(ts),
                "sequence": new_sequence, "remaining": max(0, int(units)), "original_units": max(0, int(units)), "unit_cost": max(0.0, float(rate)),
                "source_type": source_type, "source_ref": source_ref, "provisional": bool(provisional),
            }
            new_sequence += 1
            return key

        def available_layers(nm_id: int) -> list[dict[str, Any]]:
            return sorted(
                [x for x in layers.values() if int(x["nm_id"]) == int(nm_id) and int(x["remaining"]) > 0],
                key=lambda x: (x["source_ts"], int(x["sequence"])),
            )

        def consume(nm_id: int, units: int, ts: pd.Timestamp, event_id: int) -> tuple[float, list[tuple[str, int, float]], int]:
            remaining = max(0, int(units)); total = 0.0; allocs: list[tuple[str, int, float]] = []
            for layer in available_layers(nm_id):
                if remaining <= 0:
                    break
                take = min(remaining, max(0, int(layer["remaining"])))
                if take <= 0:
                    continue
                layer["remaining"] -= take
                rate = max(0.0, float(layer["unit_cost"] or 0))
                total += take * rate
                allocs.append((str(layer["key"]), take, rate))
                remaining -= take
            synthetic = 0
            if remaining > 0:
                rate = provisional_rate(nm_id, ts)
                key = add_layer(nm_id, remaining, rate, ts, "preview_synthetic_sale", f"event:{event_id}", True)
                synthetic = remaining
                layers[key]["remaining"] = 0
                total += remaining * rate
                allocs.append((key, remaining, rate))
                remaining = 0
            return round(total, 2), allocs, synthetic

        def restore(nm_id: int, units: int, ts: pd.Timestamp, matched_id: int, event_id: int) -> tuple[float, int, list[tuple[str, int, float]]]:
            remaining = max(0, int(units)); total = 0.0; restored = 0; restore_allocs: list[tuple[str, int, float]] = []
            source_allocs = sale_allocations.get(int(matched_id)) or pre_match_allocs.get(int(matched_id)) or []
            for key, alloc_units, rate in source_allocs:
                if remaining <= 0:
                    break
                take = min(remaining, max(0, int(alloc_units)))
                if take <= 0 or key not in layers:
                    continue
                layers[key]["remaining"] += take
                total += take * max(0.0, float(rate))
                restore_allocs.append((str(key), int(take), max(0.0, float(rate))))
                restored += take; remaining -= take
            if remaining > 0:
                rate = provisional_rate(nm_id, ts)
                key = add_layer(nm_id, remaining, rate, ts, "preview_return_unmatched", f"event:{event_id}", True)
                total += remaining * rate
                restore_allocs.append((str(key), int(remaining), max(0.0, float(rate))))
                restored += remaining; remaining = 0
            return round(total, 2), restored, restore_allocs

        replayed_sales = replayed_returns = synthetic_units = 0
        last_ts = replay_start_ts
        for item in timeline:
            last_ts = max(last_ts, pd.Timestamp(item["ts"]))
            kind = str(item["kind"])
            nm_id = int(item["nm_id"] or 0)
            if nm_id <= 0:
                continue
            if kind == "supply":
                add_layer(nm_id, int(item["units"]), float(item["unit_cost"]), item["ts"], "verified_fbw_supply_preview", f"{item['supply_id']}:{nm_id}", True)
                continue
            if kind == "manual_receipt":
                add_layer(nm_id, int(item["units"]), float(item["unit_cost"]), item["ts"], "manual_wb_receipt_preview", str(item["movement_id"]), False)
                continue
            b = bucket(nm_id)
            if kind == "sale":
                cost, allocs, synthetic = consume(nm_id, int(item["units"]), item["ts"], int(item["event_id"]))
                sale_allocations[int(item["event_id"])] = allocs
                b["replay_sales"] += int(item["units"]); b["current_cogs"] += float(item["current_cost"]); b["preview_cogs"] += cost
                replayed_sales += int(item["units"]); synthetic_units += synthetic; b["synthetic_units"] += synthetic
            elif kind == "return":
                cost, _, restore_allocs = restore(nm_id, int(item["units"]), item["ts"], int(item.get("matched_sale_event_id", 0)), int(item["event_id"]))
                return_allocations[int(item["event_id"])] = restore_allocs
                b["replay_returns"] += int(item["units"]); b["current_cogs"] -= float(item["current_cost"]); b["preview_cogs"] -= cost
                replayed_returns += int(item["units"])

        preview_units_map: dict[int, int] = {}
        preview_amount_map: dict[int, float] = {}
        for layer in layers.values():
            qty = max(0, int(layer["remaining"])); nm_id = int(layer["nm_id"])
            if qty <= 0 or nm_id <= 0:
                continue
            preview_units_map[nm_id] = preview_units_map.get(nm_id, 0) + qty
            preview_amount_map[nm_id] = preview_amount_map.get(nm_id, 0.0) + qty * max(0.0, float(layer["unit_cost"] or 0))

        current_fifo_rows = conn.execute(
            """
            SELECT nm_id,COALESCE(SUM(wb_units),0) AS units,COALESCE(SUM(wb_units*unit_cost_rub),0) AS amount
              FROM finished_goods_cost_layers WHERE status<>'reversed' GROUP BY nm_id
            """
        ).fetchall()
        current_fifo_map = {int(r["nm_id"]): int(r["units"] or 0) for r in current_fifo_rows}
        current_amount_map = {int(r["nm_id"]): float(r["amount"] or 0) for r in current_fifo_rows}

        incident = _read_wb_incident_reconciliation_with_conn(conn)
        incident_map: dict[int, dict[str, Any]] = {}
        if not incident.empty:
            for _, r in incident.iterrows():
                incident_map[int(r["nm_id"])] = r.to_dict()

        confidence_rank = {"Высокая": 4, "Средняя": 3, "Средняя−": 2, "Низкая": 1, "Нет": 0}

        def aggregate_supply_basis(nm_id: int) -> dict[str, Any]:
            records = supply_basis_rows_by_nm.get(int(nm_id), [])
            if not records:
                return {
                    "rate": 0.0, "source_label": "", "source_code": "",
                    "confidence": "", "evidence_date": "", "detail": "",
                }
            total_units = sum(max(0, int(r.get("units", 0) or 0)) for r in records)
            total_amount = sum(
                max(0, int(r.get("units", 0) or 0)) * max(0.0, float(r.get("rate", 0) or 0))
                for r in records
            )
            sources = sorted({str(r.get("source_label") or "") for r in records if str(r.get("source_label") or "")})
            codes = sorted({str(r.get("source_code") or "") for r in records if str(r.get("source_code") or "")})
            confidences = [str(r.get("confidence") or "Нет") for r in records]
            weakest = min(confidences, key=lambda x: confidence_rank.get(x, 0)) if confidences else "Нет"
            dates = sorted({str(r.get("evidence_date") or "") for r in records if str(r.get("evidence_date") or "")})
            supply_ids = sorted({int(r.get("supply_id", 0) or 0) for r in records if int(r.get("supply_id", 0) or 0) > 0})
            return {
                "rate": (total_amount / total_units) if total_units else 0.0,
                "source_label": ", ".join(sources),
                "source_code": ",".join(codes),
                "confidence": weakest,
                "evidence_date": ", ".join(dates),
                "detail": f"Поставки: {', '.join(str(x) for x in supply_ids)}" if supply_ids else "",
            }

        ids = sorted(set(current_fifo_map) | set(preview_units_map) | set(supply_by_nm) | set(per_nm) | set(incident_map))
        rows_out: list[dict[str, Any]] = []
        for nm_id in ids:
            if nm_id <= 0:
                continue
            inc = incident_map.get(nm_id, {})
            cur_fifo = max(0, int(current_fifo_map.get(nm_id, 0)))
            prv_fifo = max(0, int(preview_units_map.get(nm_id, 0)))
            contour = max(0, int(inc.get("current_contour", 0) or 0))
            candidate = max(0, int(inc.get("unconfirmed_candidate_units", inc.get("incident_candidate_units", 0)) or 0))
            current_safe = min(candidate, max(0, cur_fifo - contour))
            preview_safe = min(candidate, max(0, prv_fifo - contour))
            current_blocked = max(0, candidate - current_safe)
            preview_blocked = max(0, candidate - preview_safe)
            preview_shortfall = max(0, contour - prv_fifo)
            current_shortfall = max(0, contour - cur_fifo)
            b = per_nm.get(nm_id, {})
            current_cogs = float(b.get("current_cogs", 0.0) or 0.0)
            preview_cogs = float(b.get("preview_cogs", 0.0) or 0.0)
            info = meta.get(nm_id, {})
            supply_units = int(supply_by_nm.get(nm_id, 0))
            bridge_basis = bridge_basis_by_nm.get(nm_id, {})
            supply_basis = aggregate_supply_basis(nm_id)
            bridge_units = int(bridge_by_nm.get(nm_id, 0))
            bridge_rate = max(0.0, float(bridge_basis.get("rate", 0) or 0)) if bridge_units > 0 else 0.0
            supply_rate = max(0.0, float(supply_basis.get("rate", 0) or 0)) if supply_units > 0 else 0.0
            status = "Без изменений"
            if supply_units > 0 and preview_blocked < current_blocked:
                status = "Поставка разблокирует часть кандидата"
            elif supply_units > 0 and prv_fifo != cur_fifo:
                status = "Подтверждённая поставка меняет FIFO"
            elif preview_shortfall > 0:
                status = "После replay остаётся дефицит слоёв"
            rows_out.append({
                "nm_id": nm_id,
                "supplier_article": str(info.get("supplier_article") or inc.get("supplier_article") or ""),
                "product_name": str(info.get("product_name") or inc.get("product_name") or ""),
                "baseline_contour_bridge_units": bridge_units,
                "bridge_cost_rate_rub": round(bridge_rate, 2),
                "bridge_cost_source": str(bridge_basis.get("source_label") or ""),
                "bridge_cost_confidence": str(bridge_basis.get("confidence") or ""),
                "bridge_cost_evidence_date": str(bridge_basis.get("evidence_date") or ""),
                "confirmed_fbw_units": supply_units,
                "provisional_supply_rate_rub": round(supply_rate or provisional_rate(nm_id, replay_start), 2),
                "provisional_supply_cost_rub": round(float(supply_cost_by_nm.get(nm_id, 0.0)), 2),
                "supply_cost_source": str(supply_basis.get("source_label") or ""),
                "supply_cost_confidence": str(supply_basis.get("confidence") or ""),
                "supply_cost_evidence_date": str(supply_basis.get("evidence_date") or ""),
                "supply_cost_detail": str(supply_basis.get("detail") or ""),
                "forecast_total_cost_rub": round(max(0.0, float(conn.execute("SELECT COALESCE(forecast_total_cost_rub,0) FROM costs WHERE nm_id=?", (nm_id,)).fetchone()[0] or 0)) if conn.execute("SELECT 1 FROM costs WHERE nm_id=?", (nm_id,)).fetchone() else 0.0, 2),
                "replay_sales": int(b.get("replay_sales", 0) or 0),
                "replay_returns": int(b.get("replay_returns", 0) or 0),
                "synthetic_units_needed": int(b.get("synthetic_units", 0) or 0),
                "current_fifo_units": cur_fifo,
                "preview_fifo_units": prv_fifo,
                "fifo_units_delta": prv_fifo - cur_fifo,
                "current_contour_units": contour,
                "current_fifo_minus_contour": cur_fifo - contour,
                "preview_fifo_minus_contour": prv_fifo - contour,
                "incident_candidate_units": candidate,
                "current_safe_post_units": current_safe,
                "preview_safe_post_units": preview_safe,
                "safe_post_delta_units": preview_safe - current_safe,
                "current_blocked_units": current_blocked,
                "preview_blocked_units": preview_blocked,
                "current_layer_shortfall_units": current_shortfall,
                "preview_layer_shortfall_units": preview_shortfall,
                "current_replay_cogs_rub": round(current_cogs, 2),
                "preview_replay_cogs_rub": round(preview_cogs, 2),
                "replay_cogs_delta_rub": round(preview_cogs - current_cogs, 2),
                "status": status,
            })

        out = pd.DataFrame(rows_out)
        if not out.empty:
            out = out.sort_values(
                ["confirmed_fbw_units", "safe_post_delta_units", "incident_candidate_units", "supplier_article"],
                ascending=[False, False, False, True],
            ).reset_index(drop=True)

        current_fifo_units = int(sum(current_fifo_map.values()))
        preview_fifo_units = int(sum(preview_units_map.values()))
        current_contour_units = int(ctx.get("wb_contour_units", 0) or 0)
        current_candidate_units = int(pd.to_numeric(incident.get("unconfirmed_candidate_units", 0), errors="coerce").fillna(0).sum()) if not incident.empty else 0
        current_safe_units = int(pd.to_numeric(incident.get("safe_post_now_units", 0), errors="coerce").fillna(0).sum()) if not incident.empty else 0
        preview_safe_units = int(out["preview_safe_post_units"].sum()) if not out.empty else 0
        preview_blocked_units = int(out["preview_blocked_units"].sum()) if not out.empty else 0
        preview_shortfall_units = int(out["preview_layer_shortfall_units"].sum()) if not out.empty else 0
        current_cogs_total = float(out["current_replay_cogs_rub"].sum()) if not out.empty else 0.0
        preview_cogs_total = float(out["preview_replay_cogs_rub"].sum()) if not out.empty else 0.0
        verified_units = int(sum(int(g["quantity"] or 0) for g in supply_goods))
        verified_cost = float(sum(float(supply_cost_by_nm.get(nm, 0.0)) for nm in supply_cost_by_nm))

        basis_units_by_code: dict[str, int] = {}
        basis_amount_by_code: dict[str, float] = {}
        for nm_id, units in bridge_by_nm.items():
            basis = bridge_basis_by_nm.get(int(nm_id), {})
            code = str(basis.get("source_code") or "missing")
            rate = max(0.0, float(basis.get("rate", 0) or 0))
            qty = max(0, int(units or 0))
            basis_units_by_code[code] = basis_units_by_code.get(code, 0) + qty
            basis_amount_by_code[code] = basis_amount_by_code.get(code, 0.0) + qty * rate
        for records in supply_basis_rows_by_nm.values():
            for basis in records:
                code = str(basis.get("source_code") or "missing")
                rate = max(0.0, float(basis.get("rate", 0) or 0))
                qty = max(0, int(basis.get("units", 0) or 0))
                basis_units_by_code[code] = basis_units_by_code.get(code, 0) + qty
                basis_amount_by_code[code] = basis_amount_by_code.get(code, 0.0) + qty * rate
        reconstructed_cost_units = int(sum(basis_units_by_code.values()))
        reconstructed_cost_amount = float(sum(basis_amount_by_code.values()))
        actual_cost_units = int(basis_units_by_code.get("actual_production", 0))
        historical_fifo_cost_units = int(basis_units_by_code.get("historical_fifo", 0) + basis_units_by_code.get("historical_opening_fifo", 0))
        baseline_cost_units = int(basis_units_by_code.get("baseline_cost", 0))
        forecast_cost_units = int(basis_units_by_code.get("forecast_cost", 0) + basis_units_by_code.get("current_fifo_weighted", 0))
        missing_cost_units = int(basis_units_by_code.get("missing", 0))

        warnings: list[str] = []
        if baseline_bridge_units > 0:
            warnings.append(
                f"Preview добавляет {baseline_bridge_units} ед. мостового слоя на момент детального снимка, чтобы исторический FIFO покрывал полный контур WB, а не только quantity. Стоимость мостового слоя оценочная."
            )
        if synthetic_units > 0:
            warnings.append(f"Во время preview потребовалось {synthetic_units} временных единиц: подтверждённых слоёв всё ещё недостаточно для части продаж.")
        if ignored_future_units > 0:
            warnings.append(
                f"Из исторического старта исключено {ignored_future_layers} слоёв ({ignored_future_units} ед.), созданных уже после начала replay; "
                f"из них {ignored_auto_units} ед. — auto/synthetic stopgap-слои старой логики."
            )
        if actual_cost_units < reconstructed_cost_units:
            warnings.append(
                f"Фактические закрытые производственные партии покрывают {actual_cost_units} из {reconstructed_cost_units} реконструируемых единиц. "
                "Для остальных v6.5 использует исторический FIFO/базовую ставку как доказуемый оценочный cost basis, не выдавая его за факт партии."
            )
        if missing_cost_units > 0:
            warnings.append(f"Для {missing_cost_units} ед. не найдено ни одного источника себестоимости; применять реконструкцию до заполнения ставки нельзя.")
        warnings.append("Количество и factDate FBW-поставок подтверждены API; v6.5 лишь реконструирует cost basis. Никакие FIFO-слои этим preview не записываются.")

        summary = {
            "ready": True,
            "baseline_snapshot_at": baseline_at,
            "current_snapshot_at": current_at,
            "replay_start": replay_start,
            "earliest_verified_supply_at": earliest_supply_at,
            "replay_end": pd.Timestamp(last_ts).isoformat(),
            "verified_supply_count": len(supplies),
            "verified_supply_units": verified_units,
            "verified_supply_provisional_cost_rub": round(verified_cost, 2),
            "starting_fifo_units_before_bridge": int(starting_units_before_bridge),
            "baseline_contour_bridge_units": int(baseline_bridge_units),
            "baseline_contour_bridge_provisional_cost_rub": round(baseline_bridge_cost, 2),
            "cost_basis_units": reconstructed_cost_units,
            "cost_basis_amount_rub": round(reconstructed_cost_amount, 2),
            "cost_basis_actual_units": actual_cost_units,
            "cost_basis_historical_fifo_units": historical_fifo_cost_units,
            "cost_basis_baseline_units": baseline_cost_units,
            "cost_basis_forecast_units": forecast_cost_units,
            "cost_basis_missing_units": missing_cost_units,
            "cost_basis_actual_share": round((actual_cost_units / reconstructed_cost_units) if reconstructed_cost_units else 0.0, 4),
            "cost_basis_codes": dict(basis_units_by_code),
            "starting_fifo_units": int(starting_units),
            "replayed_sales": int(replayed_sales),
            "replayed_returns": int(replayed_returns),
            "replayed_events": int(replayed_sales + replayed_returns),
            "synthetic_units_needed": int(synthetic_units),
            "ignored_future_layers": int(ignored_future_layers),
            "ignored_future_layer_units": int(ignored_future_units),
            "ignored_auto_layer_units": int(ignored_auto_units),
            "current_fifo_units": current_fifo_units,
            "preview_fifo_units": preview_fifo_units,
            "fifo_units_delta": preview_fifo_units - current_fifo_units,
            "current_contour_units": current_contour_units,
            "current_fifo_minus_contour": current_fifo_units - current_contour_units,
            "preview_fifo_minus_contour": preview_fifo_units - current_contour_units,
            "candidate_units": current_candidate_units,
            "current_safe_post_units": current_safe_units,
            "preview_safe_post_units": preview_safe_units,
            "preview_blocked_units": preview_blocked_units,
            "preview_layer_shortfall_units": preview_shortfall_units,
            "preview_restoration_needed_units": int(preview_blocked_units + preview_shortfall_units),
            "current_replay_cogs_rub": round(current_cogs_total, 2),
            "preview_replay_cogs_rub": round(preview_cogs_total, 2),
            "replay_cogs_delta_rub": round(preview_cogs_total - current_cogs_total, 2),
            "cost_status": "actual" if reconstructed_cost_units > 0 and actual_cost_units == reconstructed_cost_units else ("mixed" if actual_cost_units > 0 else "historical_estimate"),
            "warnings": warnings,
        }
        if _return_plan:
            event_plan: dict[int, dict[str, Any]] = {}
            for item in timeline:
                if str(item.get("kind")) not in {"sale", "return"}:
                    continue
                event_id = int(item.get("event_id", 0) or 0)
                if event_id <= 0:
                    continue
                allocs = sale_allocations.get(event_id, []) if str(item.get("kind")) == "sale" else return_allocations.get(event_id, [])
                event_plan[event_id] = {
                    "event_id": event_id, "kind": str(item.get("kind")), "nm_id": int(item.get("nm_id", 0) or 0),
                    "movement_id": int(item.get("movement_id", 0) or 0), "event_ts": pd.Timestamp(item.get("ts")).isoformat(),
                    "matched_sale_event_id": int(item.get("matched_sale_event_id", 0) or 0),
                    "allocations": [(str(k), int(u), float(r)) for k, u, r in allocs],
                    "fifo_cost_rub": round(sum(int(u) * float(r) for k, u, r in allocs), 2),
                }
            plan = {
                "replay_start": replay_start, "baseline_snapshot_at": baseline_at, "current_snapshot_at": current_at,
                "layers": {str(k): dict(v) for k, v in layers.items()},
                "event_plan": event_plan,
                "event_ids": sorted(event_plan),
                "max_event_id": max(event_plan) if event_plan else 0,
                "preview_fifo_units": int(preview_fifo_units),
                "cost_pending_keys": [str(k) for k,v in layers.items() if float(v.get("unit_cost",0) or 0) <= 0 and int(v.get("remaining",0) or 0) > 0],
            }
            return summary, out, plan
        return summary, out

def read_wb_final_evidence_audit() -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Final pre-Apply audit for unresolved quantity and cost evidence.

    Returns:
      summary: compact counters and replay anchor;
      missing_cost: reconstructed units that still have no cost basis;
      residual_qty: SKU-level physical contour shortfalls after the full replay;
      income_candidates: incomeID evidence for residual SKUs, including IDs whose
        overall history started before the baseline but where one of the residual
        SKUs first appears only after the baseline.

    The helper is strictly read-only. It never creates FIFO layers or incident loss.
    """
    from .core import connect
    retro_summary, retro_rows = preview_retroactive_fbw_fifo_reconstruction()
    if not retro_summary.get("ready"):
        return {"ready": False, "reason": retro_summary.get("reason", "Replay недоступен")}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    baseline_at = str(retro_summary.get("baseline_snapshot_at", "") or "")
    baseline_dt = pd.to_datetime(baseline_at, errors="coerce")
    if pd.isna(baseline_dt):
        return {"ready": False, "reason": "Некорректная дата базового снимка."}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    rows = retro_rows.copy() if isinstance(retro_rows, pd.DataFrame) else pd.DataFrame()
    if rows.empty:
        return {"ready": False, "reason": "Replay не вернул SKU-строки."}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    # --- Cost-basis holes -------------------------------------------------
    bridge_units = pd.to_numeric(rows.get("baseline_contour_bridge_units", 0), errors="coerce").fillna(0).astype(int)
    bridge_rate = pd.to_numeric(rows.get("bridge_cost_rate_rub", 0), errors="coerce").fillna(0.0)
    fbw_units = pd.to_numeric(rows.get("confirmed_fbw_units", 0), errors="coerce").fillna(0).astype(int)
    fbw_rate = pd.to_numeric(rows.get("provisional_supply_rate_rub", 0), errors="coerce").fillna(0.0)
    missing_bridge = bridge_units.where((bridge_units > 0) & (bridge_rate <= 0), 0)
    missing_fbw = fbw_units.where((fbw_units > 0) & (fbw_rate <= 0), 0)
    cost_mask = (missing_bridge + missing_fbw) > 0
    missing_cost = rows.loc[cost_mask, [c for c in [
        "nm_id","supplier_article","product_name","baseline_contour_bridge_units","bridge_cost_rate_rub",
        "bridge_cost_source","confirmed_fbw_units","provisional_supply_rate_rub","supply_cost_source",
        "current_contour_units","preview_fifo_units",
    ] if c in rows.columns]].copy()
    if not missing_cost.empty:
        missing_cost["missing_cost_units"] = (missing_bridge[cost_mask] + missing_fbw[cost_mask]).astype(int).values

    # --- Residual quantity shortfall -------------------------------------
    shortfall = pd.to_numeric(rows.get("preview_layer_shortfall_units", 0), errors="coerce").fillna(0).astype(int)
    residual_qty = rows.loc[shortfall > 0, [c for c in [
        "nm_id","supplier_article","product_name","current_contour_units","preview_fifo_units",
        "preview_layer_shortfall_units","confirmed_fbw_units",
    ] if c in rows.columns]].copy()
    residual_nm_ids = sorted({int(x) for x in residual_qty.get("nm_id", pd.Series(dtype=int)).tolist() if int(x or 0) > 0})

    with connect() as conn:
        # Enrich missing-cost rows with the exact stock evidence that caused the bridge.
        if not missing_cost.empty:
            enrich: dict[int, dict[str, Any]] = {}
            for nm_id in [int(x) for x in missing_cost["nm_id"].tolist() if int(x or 0) > 0]:
                base = conn.execute(
                    """
                    SELECT COALESCE(SUM(quantity),0) AS available,
                           COALESCE(SUM(in_way_to_client),0) AS to_client,
                           COALESCE(SUM(in_way_from_client),0) AS from_client,
                           GROUP_CONCAT(DISTINCT chrt_id) AS chrt_ids,
                           GROUP_CONCAT(DISTINCT warehouse_name) AS warehouses
                      FROM stocks WHERE snapshot_at=? AND nm_id=?
                    """,
                    (baseline_at, nm_id),
                ).fetchone()
                current = conn.execute(
                    """
                    SELECT s.snapshot_at,COALESCE(SUM(s.quantity),0) AS available,
                           COALESCE(SUM(s.in_way_to_client),0) AS to_client,
                           COALESCE(SUM(s.in_way_from_client),0) AS from_client
                      FROM stocks s
                     WHERE s.snapshot_at=(SELECT MAX(snapshot_at) FROM stocks) AND s.nm_id=?
                    """,
                    (nm_id,),
                ).fetchone()
                catalog = conn.execute("SELECT COUNT(*) AS n FROM products_catalog WHERE nm_id=?", (nm_id,)).fetchone()
                cost = conn.execute("SELECT COUNT(*) AS n FROM costs WHERE nm_id=? AND COALESCE(cost_per_wb_unit,0)>0", (nm_id,)).fetchone()
                ops = conn.execute(
                    "SELECT (SELECT COUNT(*) FROM orders WHERE nm_id=?)+(SELECT COUNT(*) FROM sales WHERE nm_id=?) AS n",
                    (nm_id, nm_id),
                ).fetchone()
                enrich[nm_id] = {
                    "baseline_available": int(base["available"] or 0) if base else 0,
                    "baseline_to_client": int(base["to_client"] or 0) if base else 0,
                    "baseline_from_client": int(base["from_client"] or 0) if base else 0,
                    "baseline_chrt_ids": str(base["chrt_ids"] or "") if base else "",
                    "baseline_warehouses": str(base["warehouses"] or "") if base else "",
                    "current_available": int(current["available"] or 0) if current else 0,
                    "current_to_client": int(current["to_client"] or 0) if current else 0,
                    "current_from_client": int(current["from_client"] or 0) if current else 0,
                    "catalog_present": "Да" if catalog and int(catalog["n"] or 0) > 0 else "Нет",
                    "cost_present": "Да" if cost and int(cost["n"] or 0) > 0 else "Нет",
                    "operational_events": int(ops["n"] or 0) if ops else 0,
                }
            for col in [
                "baseline_available","baseline_to_client","baseline_from_client","baseline_chrt_ids","baseline_warehouses",
                "current_available","current_to_client","current_from_client","catalog_present","cost_present","operational_events",
            ]:
                missing_cost[col] = missing_cost["nm_id"].map(lambda x: enrich.get(int(x), {}).get(col, ""))

        # --- incomeID audit for residual SKUs ----------------------------
        income_candidates = pd.DataFrame()
        if residual_nm_ids:
            placeholders = ",".join("?" for _ in residual_nm_ids)
            events: list[dict[str, Any]] = []
            for table, date_col, flag_col, source in [
                ("orders", "order_date", "is_cancel", "order"),
                ("sales", "sale_date", "is_return", "sale"),
            ]:
                db_rows = conn.execute(
                    f"""
                    SELECT {date_col} AS event_at,nm_id,COALESCE(supplier_article,'') AS supplier_article,
                           COALESCE({flag_col},0) AS event_flag,COALESCE(raw_json,'') AS raw_json
                      FROM {table}
                     WHERE nm_id IN ({placeholders}) AND COALESCE(raw_json,'')<>''
                    """,
                    tuple(residual_nm_ids),
                ).fetchall()
                for row in db_rows:
                    try:
                        payload = json.loads(str(row["raw_json"] or "{}"))
                    except Exception:
                        continue
                    try:
                        income_id = int(payload.get("incomeID") or 0)
                    except (TypeError, ValueError):
                        income_id = 0
                    if income_id <= 0:
                        continue
                    events.append({
                        "income_id": income_id,
                        "event_at": str(row["event_at"] or ""),
                        "nm_id": int(row["nm_id"] or payload.get("nmId") or 0),
                        "supplier_article": str(row["supplier_article"] or payload.get("supplierArticle") or ""),
                        "source": source,
                        "event_flag": int(row["event_flag"] or 0),
                        "srid": str(payload.get("srid") or payload.get("gNumber") or ""),
                    })

            if events:
                ev = pd.DataFrame(events)
                ev["event_dt"] = pd.to_datetime(ev["event_at"], errors="coerce")
                ev = ev[ev["event_dt"].notna()].copy()
                verified_rows = conn.execute(
                    """
                    SELECT supply_id,fact_date_msk,status_id,accepted,units,warehouse_name
                      FROM wb_fbw_supply_confirmations
                    """
                ).fetchall()
                verified_map = {int(r["supply_id"]): r for r in verified_rows}
                goods_rows = conn.execute(
                    "SELECT supply_id,nm_id,quantity FROM wb_fbw_supply_goods"
                ).fetchall()
                verified_residual_units: dict[tuple[int, int], int] = {}
                for g in goods_rows:
                    key = (int(g["supply_id"] or 0), int(g["nm_id"] or 0))
                    verified_residual_units[key] = verified_residual_units.get(key, 0) + int(g["quantity"] or 0)

                out_rows: list[dict[str, Any]] = []
                for income_id, group in ev.groupby("income_id"):
                    income_id = int(income_id)
                    new_skus: list[str] = []
                    active_skus: list[str] = []
                    sku_firsts: list[str] = []
                    for nm_id, sku_group in group.groupby("nm_id"):
                        nm_id = int(nm_id)
                        first_dt = sku_group["event_dt"].min()
                        last_dt = sku_group["event_dt"].max()
                        article = next((str(x) for x in sku_group["supplier_article"].tolist() if str(x)), str(nm_id))
                        if first_dt > baseline_dt:
                            new_skus.append(article)
                            sku_firsts.append(f"{article}: {first_dt.isoformat()}")
                        if last_dt > baseline_dt:
                            active_skus.append(article)
                    after = group[group["event_dt"] > baseline_dt].copy()
                    if after.empty:
                        continue

                    def unique_units(part: pd.DataFrame) -> int:
                        if part.empty:
                            return 0
                        srids = [str(x) for x in part["srid"].tolist() if str(x)]
                        return len(set(srids)) if srids else len(part)

                    vr = verified_map.get(income_id)
                    verified = bool(vr)
                    fact_date = str(vr["fact_date_msk"] or "") if vr else ""
                    accepted = bool(int(vr["accepted"] or 0)) if vr else False
                    fact_after = False
                    if fact_date:
                        fdt = pd.to_datetime(fact_date, errors="coerce")
                        fact_after = bool(pd.notna(fdt) and fdt > baseline_dt)
                    verified_qty = sum(verified_residual_units.get((income_id, nm), 0) for nm in residual_nm_ids)
                    if accepted and fact_after:
                        priority = "Уже учтено"
                    elif new_skus:
                        priority = "Проверить API"
                    else:
                        priority = "Низкий приоритет"

                    out_rows.append({
                        "income_id": income_id,
                        "priority": priority,
                        "new_residual_skus": ", ".join(sorted(set(new_skus))),
                        "active_residual_skus": ", ".join(sorted(set(active_skus))),
                        "new_residual_sku_count": len(set(new_skus)),
                        "first_residual_event_at": group["event_dt"].min().isoformat(),
                        "last_residual_event_at": group["event_dt"].max().isoformat(),
                        "first_new_sku_events": "; ".join(sku_firsts),
                        "orders_after_baseline": unique_units(after[(after["source"] == "order") & (after["event_flag"] == 0)]),
                        "sales_after_baseline": unique_units(after[(after["source"] == "sale") & (after["event_flag"] == 0)]),
                        "returns_after_baseline": unique_units(after[(after["source"] == "sale") & (after["event_flag"] == 1)]),
                        "verified": "Да" if verified else "Нет",
                        "verified_fact_date": fact_date,
                        "verified_status_id": int(vr["status_id"] or 0) if vr else 0,
                        "verified_accepted": "Да" if accepted else "Нет",
                        "verified_residual_units": int(verified_qty),
                        "warehouse_name": str(vr["warehouse_name"] or "") if vr else "",
                    })
                if out_rows:
                    income_candidates = pd.DataFrame(out_rows)
                    rank = {"Проверить API": 0, "Уже учтено": 1, "Низкий приоритет": 2}
                    income_candidates["_rank"] = income_candidates["priority"].map(rank).fillna(9)
                    income_candidates = income_candidates.sort_values(
                        ["_rank","new_residual_sku_count","income_id"], ascending=[True,False,False]
                    ).drop(columns=["_rank"]).reset_index(drop=True)

    high_priority_unverified = 0
    if 'income_candidates' in locals() and isinstance(income_candidates, pd.DataFrame) and not income_candidates.empty:
        high_priority_unverified = int((income_candidates["priority"] == "Проверить API").sum())

    audit_summary = {
        "ready": True,
        "baseline_snapshot_at": baseline_at,
        "missing_cost_units": int(missing_cost.get("missing_cost_units", pd.Series(dtype=int)).sum()) if not missing_cost.empty else 0,
        "missing_cost_skus": int(len(missing_cost)),
        "residual_units": int(pd.to_numeric(residual_qty.get("preview_layer_shortfall_units", 0), errors="coerce").fillna(0).sum()) if not residual_qty.empty else 0,
        "residual_skus": int(len(residual_qty)),
        "high_priority_unverified_income_ids": high_priority_unverified,
        "apply_ready": bool(
            (int(missing_cost.get("missing_cost_units", pd.Series(dtype=int)).sum()) if not missing_cost.empty else 0) == 0
            and (int(pd.to_numeric(residual_qty.get("preview_layer_shortfall_units", 0), errors="coerce").fillna(0).sum()) if not residual_qty.empty else 0) == 0
        ),
    }
    return audit_summary, missing_cost.reset_index(drop=True), residual_qty.reset_index(drop=True), income_candidates.reset_index(drop=True) if isinstance(income_candidates, pd.DataFrame) else pd.DataFrame()

def _v67_suspense_cost_rows(missing_cost: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split missing-cost rows into safe cost-pending suspense and hard blockers."""
    if not isinstance(missing_cost, pd.DataFrame) or missing_cost.empty:
        return pd.DataFrame(), pd.DataFrame()
    eligible_rows: list[dict[str, Any]] = []
    blocked_rows: list[dict[str, Any]] = []
    for _, row in missing_cost.iterrows():
        d = row.to_dict()
        units = max(0, int(d.get("missing_cost_units", 0) or 0))
        bridge = max(0, int(d.get("baseline_contour_bridge_units", 0) or 0))
        fbw = max(0, int(d.get("confirmed_fbw_units", 0) or 0))
        base_av = max(0, int(d.get("baseline_available", 0) or 0))
        base_to = max(0, int(d.get("baseline_to_client", 0) or 0))
        base_from = max(0, int(d.get("baseline_from_client", 0) or 0))
        cur_av = max(0, int(d.get("current_available", 0) or 0))
        cur_to = max(0, int(d.get("current_to_client", 0) or 0))
        cur_from = max(0, int(d.get("current_from_client", 0) or 0))
        catalog = str(d.get("catalog_present", "") or "").strip().casefold()
        cost = str(d.get("cost_present", "") or "").strip().casefold()
        ops = max(0, int(d.get("operational_events", 0) or 0))
        # v6.7 allows only a narrowly-defined orphan-in-transit unit to remain cost_pending:
        # it must be a baseline bridge (not a verified FBW receipt), absent from catalog/costs,
        # have no order/sale history, and remain outside available stock both at the anchor and now.
        eligible = bool(
            units > 0 and bridge >= units and fbw == 0
            and base_av == 0 and cur_av == 0
            and (base_to + base_from) >= units and (cur_to + cur_from) >= units
            and catalog in {"нет", "no", "false", "0", ""}
            and cost in {"нет", "no", "false", "0", ""}
            and ops == 0
        )
        d["suspense_eligible"] = "Да" if eligible else "Нет"
        d["suspense_reason"] = (
            "Орфанная единица без каталога/ставки и без операций, остаётся только в WB-контуре вне доступного остатка."
            if eligible else "Требуется фактический cost basis до Apply."
        )
        (eligible_rows if eligible else blocked_rows).append(d)
    return pd.DataFrame(eligible_rows), pd.DataFrame(blocked_rows)

def read_fifo_reconstruction_v67_readiness() -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """Read-only readiness gate for the v6.7 atomic Apply."""
    from .core import connect
    audit, missing_cost, residual_qty, income = read_wb_final_evidence_audit()
    try:
        retro = preview_retroactive_fbw_fifo_reconstruction()
        retro_summary = retro[0] if isinstance(retro, tuple) and len(retro) >= 1 else {"ready": False}
    except Exception as exc:
        retro_summary = {"ready": False, "reason": str(exc)}
    suspense, blockers = _v67_suspense_cost_rows(missing_cost)
    with connect() as conn:
        applied = conn.execute(
            "SELECT * FROM fifo_reconstruction_apply_runs WHERE status='applied' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        incident_loss = conn.execute(
            "SELECT COALESCE(SUM(confirmed_units),0) AS units FROM wb_incident_loss_lines WHERE status='applied'"
        ).fetchone()
    residual_units = int(audit.get("residual_units", 0) or 0) if audit.get("ready") else -1
    suspense_units = int(pd.to_numeric(suspense.get("missing_cost_units", 0), errors="coerce").fillna(0).sum()) if not suspense.empty else 0
    blocker_units = int(pd.to_numeric(blockers.get("missing_cost_units", 0), errors="coerce").fillna(0).sum()) if not blockers.empty else 0
    synthetic = int(retro_summary.get("synthetic_units_needed", 0) or 0) if retro_summary.get("ready") else -1
    high_income = int(audit.get("high_priority_unverified_income_ids", 0) or 0) if audit.get("ready") else -1
    incident_units = int(incident_loss["units"] or 0) if incident_loss else 0
    already_applied = applied is not None
    ready = bool(
        audit.get("ready") and retro_summary.get("ready")
        and residual_units == 0 and blocker_units == 0 and synthetic == 0 and high_income == 0
        and incident_units == 0 and not already_applied
    )
    reasons: list[str] = []
    if not audit.get("ready"): reasons.append(str(audit.get("reason", "Финальный аудит недоступен")))
    if not retro_summary.get("ready"): reasons.append(str(retro_summary.get("reason", "Replay недоступен")))
    if residual_units > 0: reasons.append(f"Остаётся {residual_units} ед. количественного shortfall.")
    if blocker_units > 0: reasons.append(f"Для {blocker_units} ед. нет допустимого cost basis/suspense-исключения.")
    if synthetic > 0: reasons.append(f"Replay требует {synthetic} synthetic-ед.; Apply запрещён.")
    if high_income > 0: reasons.append(f"Есть {high_income} непроверенных incomeID-кандидатов.")
    if incident_units > 0: reasons.append("Уже проведены складские утраты; сначала отмените их перед реконструкцией.")
    if already_applied: reasons.append(f"Реконструкция уже применена run #{int(applied['id'])}.")
    summary = {
        "ready": ready,
        "reason": " ".join(reasons),
        "candidate_loss_units": int(retro_summary.get("candidate_units", 0) or 0),
        "expected_fifo_units": int(retro_summary.get("preview_fifo_units", 0) or 0),
        "current_fifo_units": int(retro_summary.get("current_fifo_units", 0) or 0),
        "bridge_units": int(retro_summary.get("baseline_contour_bridge_units", 0) or 0),
        "verified_supply_units": int(retro_summary.get("verified_supply_units", 0) or 0),
        "replay_sales": int(retro_summary.get("replayed_sales", 0) or 0),
        "replay_returns": int(retro_summary.get("replayed_returns", 0) or 0),
        "reconstructed_cost_rub": float(retro_summary.get("cost_basis_amount_rub", 0) or 0),
        "suspense_units": suspense_units,
        "hard_blocker_units": blocker_units,
        "residual_units": residual_units,
        "synthetic_units": synthetic,
        "high_priority_income_ids": high_income,
        "active_incident_loss_units": incident_units,
        "already_applied_run_id": int(applied["id"]) if applied else 0,
        "baseline_snapshot_at": str(retro_summary.get("baseline_snapshot_at", "") or ""),
        "current_snapshot_at": str(retro_summary.get("current_snapshot_at", "") or ""),
    }
    return summary, suspense.reset_index(drop=True), blockers.reset_index(drop=True)

def _v67_create_sqlite_backup(run_id: int) -> str:
    from .core import connect
    db_path = Path(DB_PATH).expanduser().resolve()
    backup_dir = db_path.parent / "reconstruction_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"wb_dashboard_before_v67_run{int(run_id)}_{stamp}.sqlite3"
    src = sqlite3.connect(str(db_path), timeout=30)
    dst = sqlite3.connect(str(backup_path), timeout=30)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close(); src.close()
    return str(backup_path)

def _v67_plan_hash(summary: dict[str, Any], plan: dict[str, Any]) -> str:
    compact_layers = []
    for key, layer in sorted((plan.get("layers") or {}).items()):
        compact_layers.append([
            str(key), int(layer.get("db_layer_id", 0) or 0), int(layer.get("nm_id", 0) or 0),
            int(layer.get("original_units", 0) or 0), int(layer.get("remaining", 0) or 0),
            round(float(layer.get("unit_cost", 0) or 0), 6), str(layer.get("source_type", "")),
            str(layer.get("source_ref", "")), str(layer.get("source_ts", "")),
        ])
    compact_events = []
    for event_id, e in sorted((plan.get("event_plan") or {}).items()):
        compact_events.append([
            int(event_id), str(e.get("kind", "")), int(e.get("movement_id", 0) or 0), int(e.get("nm_id", 0) or 0),
            round(float(e.get("fifo_cost_rub", 0) or 0), 2),
            [[str(k), int(u), round(float(r), 6)] for k, u, r in e.get("allocations", [])],
        ])
    payload = {
        "baseline": summary.get("baseline_snapshot_at", ""), "current": summary.get("current_snapshot_at", ""),
        "preview_fifo": int(summary.get("preview_fifo_units", 0) or 0), "candidate": int(summary.get("candidate_units", 0) or 0),
        "layers": compact_layers, "events": compact_events,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()

def read_fifo_reconstruction_apply_runs(limit: int = 20) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            "SELECT * FROM fifo_reconstruction_apply_runs ORDER BY id DESC LIMIT ?", conn, params=(max(1, int(limit)),)
        )

def apply_retroactive_fifo_reconstruction_v67(confirm_text: str) -> dict[str, Any]:
    """Atomically apply the v6.4-v6.6 reconstruction after a consistent backup.

    This changes only finished-goods FIFO layers/allocations and FIFO costs of already
    applied sales/returns. It deliberately does NOT post the warehouse-loss candidate.
    """
    from .core import connect
    from .fifo_finished_goods import _update_finished_layer_status
    readiness, suspense_df, blockers_df = read_fifo_reconstruction_v67_readiness()
    expected_confirm = f"APPLY {int(readiness.get('candidate_loss_units', 0) or 0)}"
    if str(confirm_text or "").strip().upper() != expected_confirm.upper():
        raise ValueError(f"Для применения введите точно: {expected_confirm}")
    if not readiness.get("ready"):
        raise ValueError(str(readiness.get("reason") or "Реконструкция не готова к Apply."))

    preview_result = preview_retroactive_fbw_fifo_reconstruction(_return_plan=True)
    if not isinstance(preview_result, tuple) or len(preview_result) != 3:
        raise ValueError("Не удалось построить внутренний план реконструкции.")
    summary, rows, plan = preview_result
    if not summary.get("ready"):
        raise ValueError(str(summary.get("reason") or "Replay недоступен."))
    plan_hash = _v67_plan_hash(summary, plan)
    suspense_nm = {int(x) for x in suspense_df.get("nm_id", pd.Series(dtype=int)).tolist() if int(x or 0) > 0}

    # A zero-cost suspense layer may remain on hand, but it must never have been consumed
    # by the replay. Any zero-rate allocation would corrupt historical COGS and blocks Apply.
    zero_allocs: list[tuple[int, str, int]] = []
    layers_plan = plan.get("layers") or {}
    for event_id, event in (plan.get("event_plan") or {}).items():
        for key, units, rate in event.get("allocations", []):
            layer = layers_plan.get(str(key), {})
            if int(units or 0) > 0 and float(rate or 0) <= 0:
                zero_allocs.append((int(event_id), str(key), int(layer.get("nm_id", 0) or 0)))
    if zero_allocs:
        raise ValueError("Cost-pending слой участвует в исторической продаже/возврате; Apply заблокирован до определения ставки.")

    now = datetime.now().isoformat(timespec="seconds")
    run_key = f"v67:{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    # Register the run before backup so the backup itself contains an audit row that can
    # be marked rolled_back after a full SQLite restore.
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO fifo_reconstruction_apply_runs(
                run_key,version,baseline_snapshot_at,current_snapshot_at,status,
                expected_fifo_units,candidate_loss_units,bridge_units,verified_supply_units,
                replay_sales,replay_returns,cost_pending_units,reconstructed_cost_rub,plan_hash,note,created_at
            ) VALUES(?,?,?,?, 'preparing',?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_key,"6.7",str(summary.get("baseline_snapshot_at", "") or ""),str(summary.get("current_snapshot_at", "") or ""),
                int(summary.get("preview_fifo_units", 0) or 0),int(summary.get("candidate_units", 0) or 0),
                int(summary.get("baseline_contour_bridge_units", 0) or 0),int(summary.get("verified_supply_units", 0) or 0),
                int(summary.get("replayed_sales", 0) or 0),int(summary.get("replayed_returns", 0) or 0),
                int(readiness.get("suspense_units", 0) or 0),float(summary.get("cost_basis_amount_rub", 0) or 0),
                plan_hash,"Кандидат складской утраты не проводится автоматически.",now,
            ),
        )
        run_id = int(cur.lastrowid)
    backup_path = ""
    try:
        # Store the target path first; then the backup contains the same run row/path.
        db_path = Path(DB_PATH).expanduser().resolve()
        backup_dir = db_path.parent / "reconstruction_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = str(backup_dir / f"wb_dashboard_before_v67_run{run_id}_{stamp}.sqlite3")
        with connect() as conn:
            conn.execute("UPDATE fifo_reconstruction_apply_runs SET backup_path=?,status='backup_ready' WHERE id=?", (backup_path, run_id))
        src = sqlite3.connect(str(db_path), timeout=30)
        dst = sqlite3.connect(backup_path, timeout=30)
        try:
            src.backup(dst); dst.commit()
        finally:
            dst.close(); src.close()

        conn = sqlite3.connect(str(db_path), timeout=60, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Revalidate the anchors after obtaining the write lock.
            latest = conn.execute("SELECT MAX(snapshot_at) AS x FROM stocks").fetchone()
            if str(latest["x"] or "") != str(plan.get("current_snapshot_at", "") or ""):
                raise ValueError("Снимок WB изменился после preview. Обновите страницу и повторите Apply.")
            replay_start = str(plan.get("replay_start", "") or "")
            event_rows = conn.execute(
                "SELECT id,movement_id FROM sales_fifo_events WHERE status='applied' AND datetime(event_date)>=datetime(?) ORDER BY id",
                (replay_start,),
            ).fetchall()
            db_event_ids = [int(r["id"]) for r in event_rows]
            if db_event_ids != [int(x) for x in plan.get("event_ids", [])]:
                raise ValueError("FIFO-события изменились после preview. Обновите страницу и повторите Apply.")
            movement_ids = [int(r["movement_id"] or 0) for r in event_rows]
            if any(x <= 0 for x in movement_ids):
                raise ValueError("Есть applied FIFO-событие без movement_id; автоматический Apply небезопасен.")
            active_losses = conn.execute("SELECT COALESCE(SUM(confirmed_units),0) AS u FROM wb_incident_loss_lines WHERE status='applied'").fetchone()
            if int(active_losses["u"] or 0) > 0:
                raise ValueError("Уже проведены утраты WB. Сначала отмените их.")

            # v6.7 intentionally supports the user's current history: post-anchor finished-goods
            # allocations must be the sale/return stream that is about to be replayed.
            unsupported = conn.execute(
                """
                SELECT m.movement_type,COUNT(*) AS n,COALESCE(SUM(a.units),0) AS units
                  FROM finished_goods_fifo_allocations a
                  JOIN inventory_movements m ON m.id=a.movement_id
                 WHERE a.status='applied' AND datetime(COALESCE(m.movement_date,m.created_at))>=datetime(?)
                   AND m.movement_type NOT IN ('wb_sale_fifo','wb_return_fifo')
                 GROUP BY m.movement_type
                """,
                (replay_start,),
            ).fetchall()
            if unsupported:
                desc = ", ".join(f"{r['movement_type']}:{int(r['units'] or 0)}" for r in unsupported)
                raise ValueError(f"После якоря есть неподдержанные FIFO-движения ({desc}). Apply остановлен без изменений.")

            future_layers = conn.execute(
                """
                SELECT id,source_type,ready_units,inbound_units,wb_units
                  FROM finished_goods_cost_layers
                 WHERE status<>'reversed' AND datetime(COALESCE(created_at,source_date))>=datetime(?)
                """,
                (replay_start,),
            ).fetchall()
            if any(int(r["ready_units"] or 0) > 0 or int(r["inbound_units"] or 0) > 0 for r in future_layers):
                raise ValueError("После якоря есть активные ready/inbound-слои; v6.7 не будет их автоматически переписывать.")

            now2 = datetime.now().isoformat(timespec="seconds")
            # Reverse the old post-anchor allocation ledger but retain it for audit.
            if movement_ids:
                ph = ",".join("?" for _ in movement_ids)
                conn.execute(
                    f"UPDATE finished_goods_fifo_allocations SET status='reversed',reversed_at=? WHERE status='applied' AND movement_id IN ({ph})",
                    (now2, *movement_ids),
                )

            # Old layers created after the anchor were not part of the trustworthy start;
            # mark them reversed instead of deleting them.
            future_ids = [int(r["id"]) for r in future_layers]
            if future_ids:
                ph = ",".join("?" for _ in future_ids)
                conn.execute(
                    f"UPDATE finished_goods_cost_layers SET ready_units=0,inbound_units=0,wb_units=0,status='reversed',updated_at=? WHERE id IN ({ph})",
                    (now2, *future_ids),
                )

            # Existing pre-anchor layers keep their IDs. Set only the WB balance to the
            # replayed ending balance; ready/inbound balances are left untouched.
            layer_id_map: dict[str, int] = {}
            for key, layer in layers_plan.items():
                db_layer_id = int(layer.get("db_layer_id", 0) or 0)
                if db_layer_id <= 0:
                    continue
                remaining = max(0, int(layer.get("remaining", 0) or 0))
                conn.execute("UPDATE finished_goods_cost_layers SET wb_units=?,updated_at=? WHERE id=?", (remaining, now2, db_layer_id))
                _update_finished_layer_status(conn, db_layer_id)
                layer_id_map[str(key)] = db_layer_id

            # Metadata for newly reconstructed historical layers.
            rows_df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame()
            meta_map: dict[int, dict[str, str]] = {}
            if not rows_df.empty:
                for _, rr in rows_df.iterrows():
                    nm = int(rr.get("nm_id", 0) or 0)
                    if nm > 0:
                        meta_map[nm] = {"article": str(rr.get("supplier_article", "") or ""), "product": str(rr.get("product_name", "") or "")}

            new_layers = 0; new_units = 0; cost_pending_units = 0
            for key, layer in sorted(layers_plan.items(), key=lambda kv: (str(kv[1].get("source_ts", "")), int(kv[1].get("sequence", 0) or 0))):
                if int(layer.get("db_layer_id", 0) or 0) > 0:
                    continue
                source_type_preview = str(layer.get("source_type", "") or "")
                if source_type_preview == "preview_synthetic_sale":
                    raise ValueError("План содержит synthetic-sale слой; Apply запрещён.")
                nm_id = int(layer.get("nm_id", 0) or 0)
                original = max(0, int(layer.get("original_units", 0) or 0))
                remaining = max(0, int(layer.get("remaining", 0) or 0))
                rate = max(0.0, float(layer.get("unit_cost", 0) or 0))
                if nm_id <= 0 or original <= 0:
                    continue
                if rate <= 0:
                    if nm_id not in suspense_nm:
                        raise ValueError(f"nmID {nm_id}: нулевая ставка не разрешена как suspense.")
                    source_type = "reconstruction_cost_pending"
                    cost_pending_units += remaining
                elif source_type_preview == "baseline_contour_preview":
                    source_type = "reconstruction_bridge"
                elif source_type_preview == "verified_fbw_supply_preview":
                    source_type = "verified_fbw_supply"
                elif source_type_preview == "manual_wb_receipt_preview":
                    source_type = "reconstruction_manual_receipt"
                elif source_type_preview == "preview_return_unmatched":
                    source_type = "reconstruction_return_unmatched"
                else:
                    source_type = "reconstruction_layer"
                info = meta_map.get(nm_id, {})
                article = str(info.get("article", "") or "")
                product = str(info.get("product", "") or "")
                source_ts = _wb_msk_naive_iso(layer.get("source_ts")) or str(layer.get("source_ts", "") or "")
                source_ref = f"v67:{run_id}:{key}"
                status = "active" if remaining > 0 else "depleted"
                note = (
                    f"v6.7 историческая реконструкция; preview_source={source_type_preview}; plan_hash={plan_hash[:12]}. "
                    + ("Себестоимость не определена; слой исключён из автоматического COGS." if rate <= 0 else "")
                )
                cur = conn.execute(
                    """
                    INSERT INTO finished_goods_cost_layers(
                        nm_id,supplier_article,product_name,source_type,source_ref,source_date,
                        original_units,ready_units,inbound_units,wb_units,unit_cost_rub,original_amount_rub,
                        status,note,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,0,0,?,?,?,?,?,?,?)
                    """,
                    (nm_id,article,product,source_type,source_ref,source_ts,original,remaining,rate,round(original*rate,2),status,note,now2,now2),
                )
                layer_id_map[str(key)] = int(cur.lastrowid)
                new_layers += 1; new_units += original

            # Rebuild post-anchor allocations against the reconstructed layer chronology.
            for event_id in plan.get("event_ids", []):
                event = (plan.get("event_plan") or {}).get(int(event_id)) or (plan.get("event_plan") or {}).get(str(event_id))
                if not event:
                    raise ValueError(f"Нет плана для FIFO-события {event_id}.")
                movement_id = int(event.get("movement_id", 0) or 0)
                kind = str(event.get("kind", "") or "")
                total_cost = 0.0; total_units = 0
                for key, units, rate in event.get("allocations", []):
                    units = max(0, int(units or 0)); rate = max(0.0, float(rate or 0))
                    if units <= 0:
                        continue
                    layer_id = int(layer_id_map.get(str(key), 0) or 0)
                    if layer_id <= 0:
                        raise ValueError(f"Не найден реальный layer_id для {key}.")
                    if rate <= 0:
                        raise ValueError("Нулевая ставка попала в исторический COGS; Apply остановлен.")
                    amount = round(units * rate, 2)
                    if kind == "sale":
                        from_loc, to_loc = "wb", "out"
                    else:
                        from_loc, to_loc = "customer", "wb"
                    conn.execute(
                        """
                        INSERT INTO finished_goods_fifo_allocations(
                            movement_id,layer_id,units,amount_rub,from_location,to_location,status,created_at,reversed_at
                        ) VALUES(?,?,?,?,?,?,'applied',?,NULL)
                        """,
                        (movement_id,layer_id,units,amount,from_loc,to_loc,now2),
                    )
                    total_cost += amount; total_units += units
                if total_units <= 0:
                    raise ValueError(f"FIFO-событие {event_id} после replay не имеет allocations.")
                total_cost = round(total_cost, 2)
                unit_cost = round(total_cost / total_units, 6) if total_units else 0.0
                old_event = conn.execute("SELECT note FROM sales_fifo_events WHERE id=?", (int(event_id),)).fetchone()
                old_note = str(old_event["note"] or "") if old_event else ""
                recon_note = (old_note + " | " if old_note else "") + f"FIFO пересобран v6.7 run #{run_id}."
                conn.execute(
                    "UPDATE sales_fifo_events SET fifo_cost_rub=?,unit_cost_rub=?,status='applied',note=?,updated_at=? WHERE id=?",
                    (total_cost,unit_cost,recon_note,now2,int(event_id)),
                )
                conn.execute(
                    "UPDATE inventory_movements SET goods_cost_rub=?,goods_unit_cost_rub=?,note=? WHERE id=?",
                    (total_cost,unit_cost,recon_note,movement_id),
                )

            # Final invariants before COMMIT.
            actual = conn.execute(
                "SELECT COALESCE(SUM(wb_units),0) AS u FROM finished_goods_cost_layers WHERE status<>'reversed'"
            ).fetchone()
            actual_units = int(actual["u"] or 0)
            expected_units = int(summary.get("preview_fifo_units", 0) or 0)
            if actual_units != expected_units:
                raise ValueError(f"Финальная проверка FIFO не прошла: {actual_units} != preview {expected_units}.")
            planned_by_nm: dict[int, int] = {}
            for layer in layers_plan.values():
                nm = int(layer.get("nm_id", 0) or 0); rem = max(0, int(layer.get("remaining", 0) or 0))
                if nm > 0 and rem > 0:
                    planned_by_nm[nm] = planned_by_nm.get(nm, 0) + rem
            db_rows = conn.execute(
                "SELECT nm_id,COALESCE(SUM(wb_units),0) AS u FROM finished_goods_cost_layers WHERE status<>'reversed' GROUP BY nm_id"
            ).fetchall()
            actual_by_nm = {int(r["nm_id"]): int(r["u"] or 0) for r in db_rows if int(r["u"] or 0) > 0}
            if actual_by_nm != planned_by_nm:
                mismatches = []
                for nm in sorted(set(actual_by_nm) | set(planned_by_nm)):
                    if actual_by_nm.get(nm,0) != planned_by_nm.get(nm,0):
                        mismatches.append(f"{nm}:{actual_by_nm.get(nm,0)}/{planned_by_nm.get(nm,0)}")
                raise ValueError("По-SKU проверка не прошла: " + ", ".join(mismatches[:8]))
            negative = conn.execute(
                "SELECT COUNT(*) AS n FROM finished_goods_cost_layers WHERE ready_units<0 OR inbound_units<0 OR wb_units<0"
            ).fetchone()
            if int(negative["n"] or 0) > 0:
                raise ValueError("После реконструкции обнаружен отрицательный баланс слоя.")
            pending = conn.execute(
                "SELECT COALESCE(SUM(wb_units),0) AS u FROM finished_goods_cost_layers WHERE status<>'reversed' AND source_type='reconstruction_cost_pending'"
            ).fetchone()
            if int(pending["u"] or 0) != int(readiness.get("suspense_units", 0) or 0):
                raise ValueError("Количество cost_pending после реконструкции не совпало с финальным аудитом.")

            conn.execute(
                """
                UPDATE fifo_reconstruction_apply_runs
                   SET status='applied',applied_fifo_units=?,cost_pending_units=?,applied_at=?,error_text=''
                 WHERE id=?
                """,
                (actual_units,int(pending["u"] or 0),now2,run_id),
            )
            conn.execute(
                "INSERT OR REPLACE INTO app_meta(key,value,updated_at) VALUES('fifo_reconstruction_v67_applied',?,?)",
                (json.dumps({"run_id":run_id,"plan_hash":plan_hash,"fifo_units":actual_units,"backup_path":backup_path},ensure_ascii=False),now2),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return {
            "ok": True, "run_id": run_id, "backup_path": backup_path,
            "fifo_units": int(summary.get("preview_fifo_units", 0) or 0),
            "candidate_loss_units": int(summary.get("candidate_units", 0) or 0),
            "bridge_units": int(summary.get("baseline_contour_bridge_units", 0) or 0),
            "verified_supply_units": int(summary.get("verified_supply_units", 0) or 0),
            "replay_sales": int(summary.get("replayed_sales", 0) or 0),
            "replay_returns": int(summary.get("replayed_returns", 0) or 0),
            "cost_pending_units": int(readiness.get("suspense_units", 0) or 0),
            "new_layers": new_layers, "new_original_units": new_units, "plan_hash": plan_hash,
        }
    except Exception as exc:
        with connect() as conn:
            conn.execute(
                "UPDATE fifo_reconstruction_apply_runs SET status='failed',error_text=? WHERE id=?",
                (str(exc)[:1500],run_id),
            )
        raise

def rollback_retroactive_fifo_reconstruction_v67(run_id: int, confirm_text: str) -> dict[str, Any]:
    """Restore the whole SQLite database from the automatic pre-Apply backup."""
    from .core import connect, init_db
    rid = int(run_id)
    expected = f"ROLLBACK {rid}"
    if str(confirm_text or "").strip().upper() != expected.upper():
        raise ValueError(f"Для отката введите точно: {expected}")
    with connect() as conn:
        row = conn.execute("SELECT * FROM fifo_reconstruction_apply_runs WHERE id=?", (rid,)).fetchone()
        if row is None or str(row["status"] or "") != "applied":
            raise ValueError("Этот run не находится в статусе applied.")
        backup_path = str(row["backup_path"] or "")
    if not backup_path or not Path(backup_path).exists():
        raise ValueError("Файл резервной копии перед Apply не найден.")
    db_path = Path(DB_PATH).expanduser().resolve()
    backup_dir = db_path.parent / "reconstruction_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    safety_path = backup_dir / f"wb_dashboard_before_rollback_v67_run{rid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite3"
    # Safety backup of the current applied state.
    src_now = sqlite3.connect(str(db_path), timeout=30); dst_now = sqlite3.connect(str(safety_path), timeout=30)
    try:
        src_now.backup(dst_now); dst_now.commit()
    finally:
        dst_now.close(); src_now.close()
    # Restore the consistent pre-Apply image.
    src = sqlite3.connect(backup_path, timeout=30); dst = sqlite3.connect(str(db_path), timeout=60)
    try:
        src.backup(dst); dst.commit()
    finally:
        dst.close(); src.close()
    init_db()
    with connect() as conn:
        conn.execute(
            "UPDATE fifo_reconstruction_apply_runs SET status='rolled_back',rolled_back_at=?,note=COALESCE(note,'')||? WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"),f" | Откат выполнен; safety backup applied-state: {safety_path}",rid),
        )
    return {"ok": True, "run_id": rid, "restored_from": backup_path, "safety_backup": str(safety_path)}

