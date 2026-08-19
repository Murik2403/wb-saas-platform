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


def create_wb_incident_case(
    incident_name: str, incident_date: str, warehouse_label: str = "", document_ref: str = "", note: str = "",
) -> int:
    from .core import connect
    name = str(incident_name or "").strip()
    if not name:
        raise ValueError("Укажите название инцидента.")
    event_date = str(incident_date or "").strip()
    if not event_date:
        raise ValueError("Укажите дату инцидента.")
    now = datetime.now().isoformat(timespec="seconds")
    key_seed = f"{event_date}|{warehouse_label}|{name}|{now}"
    import hashlib
    incident_key = "wb_incident:" + hashlib.sha1(key_seed.encode("utf-8")).hexdigest()[:20]
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO wb_incident_cases(
                incident_key,incident_name,incident_date,warehouse_label,document_ref,status,note,created_at,updated_at
            ) VALUES (?,?,?,?,?,'open',?,?,?)
            """,
            (incident_key,name,event_date,str(warehouse_label or ""),str(document_ref or ""),str(note or ""),now,now),
        )
        return int(cur.lastrowid)

def update_wb_incident_case(
    case_id: int, document_ref: str | None = None, note: str | None = None, status: str | None = None,
) -> None:
    from .core import connect
    assignments: list[str] = []
    params: list[Any] = []
    if document_ref is not None:
        assignments.append("document_ref=?")
        params.append(str(document_ref or ""))
    if note is not None:
        assignments.append("note=?")
        params.append(str(note or ""))
    if status is not None:
        allowed = {"open", "confirmed", "closed"}
        if str(status) not in allowed:
            raise ValueError("Недопустимый статус инцидента.")
        assignments.append("status=?")
        params.append(str(status))
    if not assignments:
        return
    assignments.append("updated_at=?")
    params.append(datetime.now().isoformat(timespec="seconds"))
    params.append(int(case_id))
    with connect() as conn:
        cur = conn.execute(f"UPDATE wb_incident_cases SET {','.join(assignments)} WHERE id=?", params)
        if cur.rowcount <= 0:
            raise ValueError("Инцидент не найден.")

def read_wb_incident_cases(limit: int = 100) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            SELECT c.*,
                   COALESCE(l.loss_units,0) AS confirmed_loss_units,
                   COALESCE(l.loss_cost,0) AS confirmed_loss_cost_rub,
                   COALESCE(p.compensation,0) AS compensation_rub,
                   COALESCE(p.compensation,0)-COALESCE(l.loss_cost,0) AS incident_result_rub
              FROM wb_incident_cases c
              LEFT JOIN (
                  SELECT case_id,SUM(confirmed_units) AS loss_units,SUM(fifo_cost_rub) AS loss_cost
                    FROM wb_incident_loss_lines WHERE status='applied' GROUP BY case_id
              ) l ON l.case_id=c.id
              LEFT JOIN (
                  SELECT case_id,SUM(amount_rub) AS compensation
                    FROM wb_incident_compensations GROUP BY case_id
              ) p ON p.case_id=c.id
             ORDER BY COALESCE(c.incident_date,'') DESC,c.id DESC LIMIT ?
            """, conn, params=(max(1,int(limit)),),
        )

def read_wb_incident_loss_lines(case_id: int | None = None) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        sql = """
            SELECT l.*,c.incident_name,c.incident_date,c.warehouse_label,c.document_ref
              FROM wb_incident_loss_lines l JOIN wb_incident_cases c ON c.id=l.case_id
        """
        params: tuple[Any, ...] = ()
        if case_id is not None:
            sql += " WHERE l.case_id=?"
            params = (int(case_id),)
        sql += " ORDER BY l.id DESC"
        return pd.read_sql_query(sql, conn, params=params)

def read_wb_incident_compensations(case_id: int | None = None) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        sql = """
            SELECT p.*,c.incident_name,c.incident_date,c.warehouse_label
              FROM wb_incident_compensations p JOIN wb_incident_cases c ON c.id=p.case_id
        """
        params: tuple[Any, ...] = ()
        if case_id is not None:
            sql += " WHERE p.case_id=?"
            params = (int(case_id),)
        sql += " ORDER BY COALESCE(p.compensation_date,'') DESC,p.id DESC"
        return pd.read_sql_query(sql, conn, params=params)

def apply_wb_incident_loss(case_id: int, nm_id: int, units: int, note: str = "") -> dict[str, Any]:
    """Recognise a documented WB warehouse loss as a separate inventory loss.

    This consumes current WB FIFO layers and records the cost outside sale COGS. It
    deliberately does not rewrite historical sale COGS; if an exact historical FIFO
    replay is required, that should be a separate controlled procedure.
    """
    from .core import connect
    from .fifo_finished_goods import _move_finished_goods_fifo
    qty = max(0, int(units or 0))
    if qty <= 0:
        raise ValueError("Количество подтверждённой утраты должно быть больше нуля.")
    with connect() as conn:
        case = conn.execute("SELECT * FROM wb_incident_cases WHERE id=?", (int(case_id),)).fetchone()
        if case is None:
            raise ValueError("Инцидент не найден.")
        if not str(case["document_ref"] or "").strip():
            raise ValueError("Для списания укажите документ/основание в карточке инцидента.")
        product = conn.execute(
            """
            SELECT COALESCE(pc.supplier_article,c.supplier_article,'') AS supplier_article,
                   COALESCE(pc.product_name,'') AS product_name
              FROM (SELECT ? AS nm_id) x
              LEFT JOIN products_catalog pc ON pc.nm_id=x.nm_id
              LEFT JOIN costs c ON c.nm_id=x.nm_id
            """, (int(nm_id),),
        ).fetchone()
        article = str(product["supplier_article"] or "") if product else ""
        product_name = str(product["product_name"] or "") if product else ""
        available = conn.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN status<>'reversed' THEN wb_units ELSE 0 END),0) AS units
              FROM finished_goods_cost_layers WHERE nm_id=?
            """, (int(nm_id),),
        ).fetchone()
        wb_units = int(available["units"] or 0) if available else 0
        if qty > wb_units:
            raise ValueError(f"В текущих FIFO-слоях WB только {wb_units} ед.; списать {qty} ед. нельзя.")

        # v6.1 hard safety barrier: never let an incident write-off reduce FIFO
        # below the latest full WB contour for the SKU.  This guard lives in the
        # database layer as well as the UI, so a direct/programmatic call cannot
        # bypass it.
        latest = conn.execute("SELECT MAX(snapshot_at) AS snapshot_at FROM stocks").fetchone()
        latest_at = str(latest["snapshot_at"] or "") if latest else ""
        if not latest_at:
            raise ValueError("Нет актуального снимка остатков WB. Без него безопасное списание инцидента заблокировано.")
        current = conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(quantity,0)+COALESCE(in_way_to_client,0)+COALESCE(in_way_from_client,0)),0) AS units
              FROM stocks WHERE snapshot_at=? AND nm_id=?
            """,
            (latest_at, int(nm_id)),
        ).fetchone()
        current_contour = max(0, int(current["units"] or 0) if current else 0)
        safe_capacity = max(0, wb_units - current_contour)
        if qty > safe_capacity:
            raise ValueError(
                f"Безопасно списать сейчас не более {safe_capacity} ед.: FIFO WB {wb_units} ед., "
                f"текущий полный контур WB {current_contour} ед. После списания {qty} ед. FIFO стал бы ниже факта/контура. "
                "Сначала восстановите недостающие входящие/стоимостные слои или дождитесь уточнения данных WB."
            )
        now = datetime.now().isoformat(timespec="seconds")
        event_key = f"wb_incident_loss:{int(case_id)}:{int(nm_id)}:{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        conn.execute("SAVEPOINT wb_incident_loss")
        try:
            cur = conn.execute(
                """
                INSERT INTO inventory_movements(
                    event_key,movement_type,nm_id,supplier_article,product_name,quantity,ready_delta,inbound_delta,
                    route,movement_date,expected_arrival_date,source_task_key,reference_movement_id,status,note,
                    created_at,reversed_at,goods_cost_rub,goods_unit_cost_rub
                ) VALUES (?,?,?,?,?,?,0,0,?,?,NULL,?,NULL,'applied',?,?,NULL,0,0)
                """,
                (
                    event_key,"wb_incident_loss",int(nm_id),article,product_name,qty,
                    str(case["warehouse_label"] or "WB incident"),str(case["incident_date"] or "")[:10],
                    str(case["incident_key"] or ""),
                    f"Подтверждённая утрата WB; документ: {str(case['document_ref'] or '')}. {str(note or '')}".strip(),now,
                ),
            )
            movement_id = int(cur.lastrowid)
            cost, _ = _move_finished_goods_fifo(conn,movement_id,int(nm_id),article,product_name,qty,"wb",None)
            unit_cost = round(cost/qty,4) if qty else 0.0
            conn.execute(
                "UPDATE inventory_movements SET goods_cost_rub=?,goods_unit_cost_rub=? WHERE id=?",
                (round(cost,2),unit_cost,movement_id),
            )
            loss_cur = conn.execute(
                """
                INSERT INTO wb_incident_loss_lines(
                    case_id,nm_id,supplier_article,product_name,confirmed_units,fifo_cost_rub,unit_cost_rub,
                    movement_id,status,note,created_at,reversed_at
                ) VALUES (?,?,?,?,?,?,?,?,'applied',?,?,NULL)
                """,
                (int(case_id),int(nm_id),article,product_name,qty,round(cost,2),unit_cost,movement_id,str(note or ""),now),
            )
            conn.execute("UPDATE wb_incident_cases SET status='confirmed',updated_at=? WHERE id=?", (now,int(case_id)))
            conn.execute("RELEASE SAVEPOINT wb_incident_loss")
            return {
                "line_id": int(loss_cur.lastrowid), "movement_id": movement_id, "units": qty,
                "fifo_cost_rub": round(cost,2), "unit_cost_rub": unit_cost,
                "fifo_before_units": wb_units, "current_contour_units": current_contour,
                "safe_capacity_before_units": safe_capacity, "fifo_after_units": wb_units - qty,
            }
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT wb_incident_loss")
            conn.execute("RELEASE SAVEPOINT wb_incident_loss")
            raise

def reverse_wb_incident_loss(line_id: int) -> dict[str, Any]:
    from .core import connect
    from .inventory_movements import undo_inventory_movement
    with connect() as conn:
        line = conn.execute("SELECT * FROM wb_incident_loss_lines WHERE id=?", (int(line_id),)).fetchone()
        if line is None:
            return {"ok": False, "message": "Строка утраты не найдена."}
        if str(line["status"] or "") != "applied":
            return {"ok": False, "message": "Эта строка уже отменена."}
        movement_id = int(line["movement_id"] or 0)
    result = undo_inventory_movement(movement_id)
    if result.get("ok"):
        now = datetime.now().isoformat(timespec="seconds")
        with connect() as conn:
            conn.execute(
                "UPDATE wb_incident_loss_lines SET status='reversed',reversed_at=? WHERE id=?",
                (now,int(line_id)),
            )
            active = conn.execute(
                "SELECT COUNT(*) AS n FROM wb_incident_loss_lines WHERE case_id=? AND status='applied'",
                (int(line["case_id"]),),
            ).fetchone()
            if int(active["n"] or 0) == 0:
                conn.execute(
                    "UPDATE wb_incident_cases SET status=CASE WHEN status='closed' THEN status ELSE 'open' END,updated_at=? WHERE id=?",
                    (now,int(line["case_id"])),
                )
    return result

def record_wb_incident_compensation(
    case_id: int, amount_rub: float, compensation_date: str, source_ref: str, note: str = "", nm_id: int = 0,
) -> int:
    from .core import connect
    amount = float(amount_rub or 0)
    if amount <= 0:
        raise ValueError("Сумма компенсации должна быть больше нуля.")
    ref = str(source_ref or "").strip()
    if not ref:
        raise ValueError("Укажите документ/идентификатор компенсации WB.")
    with connect() as conn:
        case = conn.execute("SELECT id FROM wb_incident_cases WHERE id=?", (int(case_id),)).fetchone()
        if case is None:
            raise ValueError("Инцидент не найден.")
        cur = conn.execute(
            """
            INSERT INTO wb_incident_compensations(
                case_id,nm_id,amount_rub,compensation_date,source_ref,note,created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (int(case_id),int(nm_id or 0),round(amount,2),str(compensation_date or ""),ref,str(note or ""),datetime.now().isoformat(timespec="seconds")),
        )
        return int(cur.lastrowid)

def delete_wb_incident_compensation(compensation_id: int) -> bool:
    from .core import connect
    with connect() as conn:
        cur = conn.execute("DELETE FROM wb_incident_compensations WHERE id=?", (int(compensation_id),))
        return cur.rowcount > 0

def wb_incident_financial_summary(case_id: int | None = None) -> dict[str, Any]:
    from .core import connect
    with connect() as conn:
        params: tuple[Any, ...] = () if case_id is None else (int(case_id),)
        loss_where = "" if case_id is None else " AND case_id=?"
        comp_where = "" if case_id is None else " WHERE case_id=?"
        loss = conn.execute(
            f"SELECT COALESCE(SUM(confirmed_units),0) AS units,COALESCE(SUM(fifo_cost_rub),0) AS cost FROM wb_incident_loss_lines WHERE status='applied'{loss_where}",
            params,
        ).fetchone()
        comp = conn.execute(
            f"SELECT COALESCE(SUM(amount_rub),0) AS amount FROM wb_incident_compensations{comp_where}",
            params,
        ).fetchone()
        units = int(loss["units"] or 0) if loss else 0
        cost = float(loss["cost"] or 0) if loss else 0.0
        compensation = float(comp["amount"] or 0) if comp else 0.0
        return {
            "confirmed_loss_units": units,
            "confirmed_loss_cost_rub": round(cost,2),
            "compensation_rub": round(compensation,2),
            "incident_result_rub": round(compensation-cost,2),
            "unrecovered_loss_rub": round(max(0.0,cost-compensation),2),
        }
