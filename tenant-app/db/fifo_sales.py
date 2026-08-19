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


def _sales_raw_srid(raw_json: str | None) -> str:
    try:
        payload = json.loads(str(raw_json or "{}"))
    except Exception:
        return ""
    return str(payload.get("srid") or payload.get("SRID") or payload.get("odid") or "").strip()

def _initialize_sales_fifo_baseline_if_needed(conn: sqlite3.Connection) -> dict[str, Any]:
    """Register all currently stored sale/return rows as pre-FIFO history once.

    Empty databases are deliberately left uninitialized so that the first API
    synchronization can load history and establish it as the baseline instead
    of treating 90 days of old sales as new FIFO events.
    """
    meta = conn.execute("SELECT value,updated_at FROM app_meta WHERE key='sales_fifo_tracking_initialized'").fetchone()
    if meta is not None:
        return {"initialized": True, "baseline": 0, "initialized_at": str(meta["updated_at"] or "")}
    count = int(conn.execute("SELECT COUNT(*) AS n FROM sales").fetchone()["n"] or 0)
    if count <= 0:
        return {"initialized": False, "baseline": 0, "initialized_at": ""}
    now = datetime.now().isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT s.*,COALESCE(pc.product_name,'') AS product_name
          FROM sales s LEFT JOIN products_catalog pc ON pc.nm_id=s.nm_id
         ORDER BY COALESCE(s.sale_date,''),s.id
        """
    ).fetchall()
    inserted = 0
    for row in rows:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO sales_fifo_events(
                sale_record_id,sale_id,srid,nm_id,supplier_article,product_name,event_date,event_type,
                units,fifo_cost_rub,unit_cost_rub,status,movement_id,matched_sale_event_id,note,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,1,0,0,'baseline',NULL,NULL,?,?,?)
            """,
            (
                str(row["id"]),str(row["sale_id"] or ""),_sales_raw_srid(row["raw_json"]),int(row["nm_id"] or 0),
                str(row["supplier_article"] or ""),str(row["product_name"] or ""),str(row["sale_date"] or ""),
                "return" if int(row["is_return"] or 0)==1 else "sale",
                "Историческая операция до включения точного FIFO-COGS.",now,now,
            ),
        )
        inserted += int(cur.rowcount or 0)
    conn.execute(
        "INSERT INTO app_meta(key,value,updated_at) VALUES ('sales_fifo_tracking_initialized',?,?)",
        (json.dumps({"baseline_rows": inserted},ensure_ascii=False),now),
    )
    return {"initialized": True, "baseline": inserted, "initialized_at": now}

def initialize_sales_fifo_tracking() -> dict[str, Any]:
    """Initialize finished-goods layers and baseline all sale rows currently in the database."""
    from .core import connect
    from .fifo_finished_goods import initialize_finished_goods_fifo
    initialize_finished_goods_fifo(False)
    with connect() as conn:
        result = _initialize_sales_fifo_baseline_if_needed(conn)
        if not result.get("initialized"):
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "INSERT OR REPLACE INTO app_meta(key,value,updated_at) VALUES ('sales_fifo_tracking_initialized',?,?)",
                (json.dumps({"baseline_rows": 0},ensure_ascii=False),now),
            )
            result = {"initialized": True,"baseline": 0,"initialized_at": now}
        return result

def sales_fifo_tracking_status() -> dict[str, Any]:
    from .core import connect
    with connect() as conn:
        meta = conn.execute("SELECT value,updated_at FROM app_meta WHERE key='sales_fifo_tracking_initialized'").fetchone()
        counts = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status='baseline' THEN 1 ELSE 0 END) AS baseline_rows,
                   SUM(CASE WHEN status='applied' AND event_type='sale' THEN 1 ELSE 0 END) AS sales_applied,
                   SUM(CASE WHEN status='applied' AND event_type='return' THEN 1 ELSE 0 END) AS returns_applied,
                   SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
                   MAX(CASE WHEN status='applied' THEN event_date END) AS last_event_date
              FROM sales_fifo_events
            """
        ).fetchone()
        return {
            "initialized": meta is not None,
            "initialized_at": str(meta["updated_at"] or "") if meta else "",
            "total": int(counts["total"] or 0),
            "baseline_rows": int(counts["baseline_rows"] or 0),
            "sales_applied": int(counts["sales_applied"] or 0),
            "returns_applied": int(counts["returns_applied"] or 0),
            "errors": int(counts["errors"] or 0),
            "last_event_date": str(counts["last_event_date"] or ""),
        }

def _insert_sales_fifo_event(
    conn: sqlite3.Connection,row: sqlite3.Row,event_type: str,status: str,fifo_cost: float,
    movement_id: int | None,matched_sale_event_id: int | None,note: str,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    product_name = str(row["product_name"] or "") if "product_name" in row.keys() else ""
    cur = conn.execute(
        """
        INSERT INTO sales_fifo_events(
            sale_record_id,sale_id,srid,nm_id,supplier_article,product_name,event_date,event_type,
            units,fifo_cost_rub,unit_cost_rub,status,movement_id,matched_sale_event_id,note,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?)
        """,
        (
            str(row["id"]),str(row["sale_id"] or ""),_sales_raw_srid(row["raw_json"]),int(row["nm_id"] or 0),
            str(row["supplier_article"] or ""),product_name,str(row["sale_date"] or ""),event_type,
            round(max(0.0,float(fifo_cost or 0)),2),round(max(0.0,float(fifo_cost or 0)),4),status,
            movement_id,matched_sale_event_id,str(note or ""),now,now,
        ),
    )
    return int(cur.lastrowid)

def _find_matching_sale_fifo_event(conn: sqlite3.Connection,nm_id: int,srid: str) -> sqlite3.Row | None:
    if not srid:
        return None
    return conn.execute(
        """
        SELECT e.* FROM sales_fifo_events e
         WHERE e.nm_id=? AND e.srid=? AND e.event_type='sale' AND e.status IN ('applied','baseline')
           AND NOT EXISTS (
               SELECT 1 FROM sales_fifo_events r
                WHERE r.matched_sale_event_id=e.id AND r.event_type='return' AND r.status='applied'
           )
         ORDER BY CASE WHEN e.status='applied' THEN 0 ELSE 1 END,COALESCE(e.event_date,'') DESC,e.id DESC
         LIMIT 1
        """,
        (int(nm_id),str(srid)),
    ).fetchone()

def _restore_return_to_original_layers(
    conn: sqlite3.Connection,return_movement_id: int,sale_event: sqlite3.Row,units: int=1,
) -> tuple[float,int]:
    sale_movement_id = int(sale_event["movement_id"] or 0)
    if sale_movement_id <= 0:
        return 0.0,0
    allocations = conn.execute(
        """
        SELECT a.*,l.unit_cost_rub FROM finished_goods_fifo_allocations a
        JOIN finished_goods_cost_layers l ON l.id=a.layer_id
        WHERE a.movement_id=? AND a.status='applied' AND a.from_location='wb'
        ORDER BY a.id
        """,
        (sale_movement_id,),
    ).fetchall()
    remaining=max(0,int(units or 0)); total=0.0; restored=0; now=datetime.now().isoformat(timespec="seconds")
    for allocation in allocations:
        if remaining<=0: break
        take=min(remaining,max(0,int(allocation["units"] or 0)))
        if take<=0: continue
        rate=max(0.0,float(allocation["amount_rub"] or 0)/max(1,int(allocation["units"] or 1)))
        amount=round(take*rate,2)
        conn.execute(
            "UPDATE finished_goods_cost_layers SET wb_units=wb_units+?,status='active',updated_at=? WHERE id=?",
            (take,now,int(allocation["layer_id"])),
        )
        conn.execute(
            """
            INSERT INTO finished_goods_fifo_allocations(
                movement_id,layer_id,units,amount_rub,from_location,to_location,status,created_at,reversed_at
            ) VALUES (?,?,?,?, 'customer','wb','applied',?,NULL)
            """,
            (int(return_movement_id),int(allocation["layer_id"]),take,amount,now),
        )
        remaining-=take; restored+=take; total+=amount
    return round(total,2),restored

def process_sales_fifo_events(limit: int=5000) -> dict[str,Any]:
    """Consume one WB FIFO unit per new sale and restore the original layer on a return."""
    from .core import connect
    from .fifo_finished_goods import _baseline_product_cost, _create_finished_goods_layer, _move_finished_goods_fifo
    status=sales_fifo_tracking_status()
    if not status.get("initialized"):
        return {"initialized":False,"processed":0,"sales":0,"returns":0,"errors":0,"warnings":["FIFO продаж ещё не инициализирован."]}
    result={"initialized":True,"processed":0,"sales":0,"returns":0,"errors":0,"warnings":[]}
    with connect() as conn:
        rows=conn.execute(
            """
            SELECT s.*,COALESCE(pc.product_name,'') AS product_name
              FROM sales s
              LEFT JOIN sales_fifo_events e ON e.sale_record_id=s.id
              LEFT JOIN products_catalog pc ON pc.nm_id=s.nm_id
             WHERE e.id IS NULL
             ORDER BY COALESCE(s.last_change,s.sale_date,''),s.id
             LIMIT ?
            """,
            (max(1,int(limit)),),
        ).fetchall()
        for idx,row in enumerate(rows):
            savepoint=f"sale_fifo_{idx}"
            conn.execute(f"SAVEPOINT {savepoint}")
            try:
                nm_id=int(row["nm_id"] or 0); article=str(row["supplier_article"] or ""); product=str(row["product_name"] or "")
                event_type="return" if int(row["is_return"] or 0)==1 else "sale"
                source_key=str(row["id"])
                event_key=f"sales_fifo:{source_key}"
                cur=conn.execute(
                    """
                    INSERT INTO inventory_movements(
                        event_key,movement_type,nm_id,supplier_article,product_name,quantity,ready_delta,inbound_delta,
                        route,movement_date,expected_arrival_date,source_task_key,reference_movement_id,status,note,
                        created_at,reversed_at,goods_cost_rub,goods_unit_cost_rub
                    ) VALUES (?,?,?,?,?,1,0,0,'WB sale',?,NULL,?,NULL,'applied','',?,NULL,0,0)
                    """,
                    (event_key,"wb_return_fifo" if event_type=="return" else "wb_sale_fifo",nm_id,article,product,
                     str(row["sale_date"] or "")[:10],source_key,datetime.now().isoformat(timespec="seconds")),
                )
                movement_id=int(cur.lastrowid); matched_id=None; note=""
                if event_type=="sale":
                    try:
                        fifo_cost,_=_move_finished_goods_fifo(conn,movement_id,nm_id,article,product,1,"wb",None)
                    except ValueError as fifo_exc:
                        if "COST_PENDING:" in str(fifo_exc):
                            raise
                        # A sale can arrive before a matching stock/receipt event. Preserve COGS with a synthetic
                        # one-unit layer at the current baseline and expose the inventory mismatch for review.
                        baseline=_baseline_product_cost(conn,nm_id)
                        _create_finished_goods_layer(
                            conn,nm_id,article,product,"synthetic_sale",f"synthetic_sale:{source_key}",
                            str(row["sale_date"] or "")[:10],1,baseline,"wb",
                            "Временный слой для продажи без доступного FIFO-остатка; требуется сверка WB.",
                        )
                        fifo_cost,_=_move_finished_goods_fifo(conn,movement_id,nm_id,article,product,1,"wb",None)
                        note="Продажа обработана через временный слой; проверьте сверку остатков WB."
                    result["sales"]+=1
                else:
                    srid=_sales_raw_srid(row["raw_json"])
                    matched=_find_matching_sale_fifo_event(conn,nm_id,srid)
                    fifo_cost=0.0; restored=0
                    if matched is not None:
                        matched_id=int(matched["id"])
                        fifo_cost,restored=_restore_return_to_original_layers(conn,movement_id,matched,1)
                    if restored<1:
                        matched_rate=max(0.0,float(matched["unit_cost_rub"] or 0)) if matched is not None else 0.0
                        rate=matched_rate if matched_rate>0 else _baseline_product_cost(conn,nm_id)
                        _create_finished_goods_layer(
                            conn,nm_id,article,product,"return_unmatched",f"return:{source_key}",
                            str(row["sale_date"] or "")[:10],1,rate,"wb",
                            "Возврат без точного слоя исходной продажи; стоимость восстановлена по доступной ставке.",
                        )
                        fifo_cost=rate
                        note="Возврат не удалось полностью сопоставить с исходной продажей."
                    result["returns"]+=1
                conn.execute(
                    "UPDATE inventory_movements SET goods_cost_rub=?,goods_unit_cost_rub=?,note=? WHERE id=?",
                    (round(fifo_cost,2),round(fifo_cost,4),note,movement_id),
                )
                _insert_sales_fifo_event(conn,row,event_type,"applied",fifo_cost,movement_id,matched_id,note)
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                result["processed"]+=1
            except Exception as exc:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                try:
                    _insert_sales_fifo_event(
                        conn,row,"return" if int(row["is_return"] or 0)==1 else "sale","error",0.0,None,None,str(exc)[:500]
                    )
                except Exception:
                    pass
                result["errors"]+=1
                result["warnings"].append(f"{row['supplier_article'] or row['nm_id']}: {str(exc)[:180]}")
    return result

def retry_sales_fifo_errors(limit: int=5000) -> dict[str,Any]:
    from .core import connect
    with connect() as conn:
        conn.execute("DELETE FROM sales_fifo_events WHERE status='error' AND movement_id IS NULL")
    return process_sales_fifo_events(limit)

def read_sales_fifo_events(limit: int=500) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            SELECT e.*,m.goods_cost_rub AS movement_cost_rub,m.note AS movement_note
              FROM sales_fifo_events e LEFT JOIN inventory_movements m ON m.id=e.movement_id
             ORDER BY e.id DESC LIMIT ?
            """,conn,params=(max(1,int(limit)),)
        )

def read_sales_fifo_cogs(date_from: str,date_to: str) -> pd.DataFrame:
    """Per-article COGS: exact FIFO for tracked rows, baseline cost for historical/unprocessed rows."""
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            WITH base AS (
                SELECT s.id,s.nm_id,COALESCE(pc.supplier_article,s.supplier_article,c.supplier_article,'') AS supplier_article,
                       COALESCE(pc.product_name,'') AS product_name,s.sale_date,s.is_return,
                       COALESCE(c.cost_per_wb_unit,0) AS baseline_unit_cost,
                       e.status,e.fifo_cost_rub,e.event_type
                  FROM sales s
                  LEFT JOIN sales_fifo_events e ON e.sale_record_id=s.id
                  LEFT JOIN products_catalog pc ON pc.nm_id=s.nm_id
                  LEFT JOIN costs c ON c.nm_id=s.nm_id
                 WHERE DATE(s.sale_date) BETWEEN DATE(?) AND DATE(?)
            )
            SELECT nm_id,MAX(supplier_article) AS supplier_article,MAX(product_name) AS product_name,
                   SUM(CASE WHEN is_return=0 THEN 1 ELSE 0 END) AS sale_units,
                   SUM(CASE WHEN is_return=1 THEN 1 ELSE 0 END) AS return_units,
                   SUM(CASE WHEN is_return=0 THEN 1 ELSE -1 END) AS net_units,
                   SUM(CASE WHEN is_return=0 THEN baseline_unit_cost ELSE -baseline_unit_cost END) AS baseline_cogs_rub,
                   SUM(CASE WHEN status='applied'
                            THEN CASE WHEN is_return=0 THEN fifo_cost_rub ELSE -fifo_cost_rub END
                            ELSE CASE WHEN is_return=0 THEN baseline_unit_cost ELSE -baseline_unit_cost END END) AS estimated_fifo_cogs_rub,
                   SUM(CASE WHEN status='applied' THEN CASE WHEN is_return=0 THEN fifo_cost_rub ELSE -fifo_cost_rub END ELSE 0 END) AS exact_fifo_cogs_rub,
                   SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) AS covered_events,
                   COUNT(*) AS total_events,
                   SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS error_events
              FROM base GROUP BY nm_id
             ORDER BY ABS(SUM(CASE WHEN status='applied'
                            THEN CASE WHEN is_return=0 THEN fifo_cost_rub ELSE -fifo_cost_rub END
                            ELSE CASE WHEN is_return=0 THEN baseline_unit_cost ELSE -baseline_unit_cost END END)) DESC
            """,conn,params=(str(date_from),str(date_to))
        )

def read_sales_fifo_allocations(limit: int=500) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            SELECT e.id AS event_id,e.event_date,e.event_type,e.status,e.supplier_article,e.product_name,
                   e.sale_id,e.srid,e.fifo_cost_rub,e.matched_sale_event_id,e.note,
                   a.layer_id,a.units,a.amount_rub,a.from_location,a.to_location,l.source_type,l.source_ref,l.unit_cost_rub
              FROM sales_fifo_events e
              LEFT JOIN finished_goods_fifo_allocations a ON a.movement_id=e.movement_id AND a.status='applied'
              LEFT JOIN finished_goods_cost_layers l ON l.id=a.layer_id
             WHERE e.status<>'baseline'
             ORDER BY e.id DESC,a.id LIMIT ?
            """,conn,params=(max(1,int(limit)),)
        )
