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


from .wb_fifo_reconciliation import _wb_fifo_reconciliation_context_with_conn


def _wb_msk_naive_iso(value: Any) -> str:
    """Normalize WB/API timestamps to Moscow local time without timezone suffix.

    SQLite stock snapshots in this project are stored as Moscow-local naive ISO
    timestamps. Normalizing verified FBW factDate the same way makes comparisons
    deterministic and avoids tz-aware/naive errors.
    """
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return ""
    try:
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.tz_localize("Europe/Moscow")
        else:
            ts = ts.tz_convert("Europe/Moscow")
        ts = ts.tz_localize(None)
    except Exception:
        pass
    return pd.Timestamp(ts).isoformat(timespec="seconds")

def _fbw_goods_quantity(item: dict[str, Any]) -> int:
    qty = 0
    barcodes = item.get("barcodes") or []
    if isinstance(barcodes, list) and barcodes:
        for barcode_row in barcodes:
            if not isinstance(barcode_row, dict):
                continue
            try:
                qty += int(barcode_row.get("quantity") or 0)
            except (TypeError, ValueError):
                pass
    else:
        try:
            qty = int(item.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
    return max(0, qty)

def save_verified_fbw_supply(supply_id: int, detail: dict[str, Any], goods: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Persist verified FBW supply evidence from the official Supplies API.

    A supply with statusID=5 is treated as accepted stock evidence. Persisting it
    changes only the diagnostic reconstruction; it never creates FIFO cost layers.
    Repeated verification is idempotent because supply_id/nm_id are unique.
    """
    from .core import connect
    sid = int(supply_id)
    if sid <= 0:
        raise ValueError("Некорректный ID поставки.")
    detail = detail if isinstance(detail, dict) else {}
    goods = [g for g in goods if isinstance(g, dict)]
    fact_date = str(detail.get("factDate") or detail.get("fact_date") or "")
    create_date = str(detail.get("createDate") or detail.get("createdDate") or detail.get("create_date") or "")
    fact_date_msk = _wb_msk_naive_iso(fact_date)
    try:
        status_id = int(detail.get("statusID") if detail.get("statusID") is not None else detail.get("statusId") or 0)
    except (TypeError, ValueError):
        status_id = 0
    warehouse = str(detail.get("warehouseName") or detail.get("warehouse") or detail.get("warehouseID") or "")
    accepted = 1 if status_id == 5 and bool(fact_date_msk) else 0
    now = datetime.now().isoformat(timespec="seconds")

    with connect() as conn:
        article_to_nm: dict[str, int] = {}
        for row in conn.execute(
            """
            SELECT nm_id,COALESCE(supplier_article,'') AS supplier_article FROM products_catalog
            UNION ALL
            SELECT nm_id,COALESCE(supplier_article,'') FROM costs
            """
        ).fetchall():
            article = str(row["supplier_article"] or "").strip().lower()
            if article and int(row["nm_id"] or 0) > 0:
                article_to_nm.setdefault(article, int(row["nm_id"]))

        aggregated: dict[int, dict[str, Any]] = {}
        for item in goods:
            try:
                nm_id = int(item.get("nmID") or item.get("nmId") or item.get("nm_id") or 0)
            except (TypeError, ValueError):
                nm_id = 0
            article = str(item.get("vendorCode") or item.get("supplierArticle") or item.get("supplier_article") or "").strip()
            if nm_id <= 0 and article:
                nm_id = int(article_to_nm.get(article.lower(), 0) or 0)
            if nm_id <= 0:
                continue
            qty = _fbw_goods_quantity(item)
            if qty <= 0:
                continue
            row = aggregated.setdefault(nm_id, {"quantity": 0, "supplier_article": article, "raw": []})
            row["quantity"] += qty
            if article and not row["supplier_article"]:
                row["supplier_article"] = article
            row["raw"].append(item)

        units = int(sum(int(v["quantity"]) for v in aggregated.values()))
        sku_count = len(aggregated)
        conn.execute(
            """
            INSERT INTO wb_fbw_supply_confirmations(
                supply_id,fact_date,fact_date_msk,create_date,status_id,warehouse_name,
                accepted,sku_count,units,raw_json,verified_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(supply_id) DO UPDATE SET
                fact_date=excluded.fact_date,fact_date_msk=excluded.fact_date_msk,
                create_date=excluded.create_date,status_id=excluded.status_id,
                warehouse_name=excluded.warehouse_name,accepted=excluded.accepted,
                sku_count=excluded.sku_count,units=excluded.units,
                raw_json=excluded.raw_json,verified_at=excluded.verified_at
            """,
            (sid,fact_date,fact_date_msk,create_date,status_id,warehouse,accepted,sku_count,units,
             json.dumps(detail,ensure_ascii=False,default=str),now),
        )
        conn.execute("DELETE FROM wb_fbw_supply_goods WHERE supply_id=?", (sid,))
        for nm_id, row in aggregated.items():
            conn.execute(
                """
                INSERT INTO wb_fbw_supply_goods(
                    supply_id,nm_id,supplier_article,quantity,raw_json,verified_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (sid,int(nm_id),str(row["supplier_article"] or ""),int(row["quantity"]),
                 json.dumps(row["raw"],ensure_ascii=False,default=str),now),
            )

    return {
        "supply_id": sid, "fact_date": fact_date, "fact_date_msk": fact_date_msk,
        "status_id": status_id, "warehouse_name": warehouse, "accepted": bool(accepted),
        "sku_count": sku_count, "units": units,
    }

def read_verified_fbw_supplies() -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            SELECT supply_id,fact_date,fact_date_msk,create_date,status_id,warehouse_name,
                   accepted,sku_count,units,verified_at
              FROM wb_fbw_supply_confirmations
             ORDER BY fact_date_msk,supply_id
            """, conn,
        )

def read_verified_fbw_supply_goods(supply_id: int | None = None) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        where = " WHERE g.supply_id=?" if supply_id is not None else ""
        params: tuple[Any, ...] = (int(supply_id),) if supply_id is not None else ()
        return pd.read_sql_query(
            f"""
            SELECT g.supply_id,g.nm_id,COALESCE(g.supplier_article,'') AS supplier_article,
                   g.quantity,s.fact_date,s.fact_date_msk,s.status_id,s.warehouse_name,s.accepted,g.verified_at
              FROM wb_fbw_supply_goods g
              JOIN wb_fbw_supply_confirmations s ON s.supply_id=g.supply_id
              {where}
             ORDER BY s.fact_date_msk,g.supply_id,g.nm_id
            """, conn, params=params,
        )

def read_wb_income_supply_evidence() -> pd.DataFrame:
    """Return supply-linkage evidence inferred from incomeID in orders/sales.

    `incomeID` is stored inside the raw WB operational payloads.  This helper
    intentionally treats a supply as only a *candidate* when that ID is first
    observed after the last detailed stock snapshot.  First observation is not
    proof of the supply acceptance date; the FBW Supplies API must be queried to
    confirm `factDate` before the evidence is used for reconciliation.
    """
    from .core import connect
    with connect() as conn:
        ctx = _wb_fifo_reconciliation_context_with_conn(conn)
        baseline_at = str(ctx.get("last_detailed_snapshot_at", "") or "")
        if not baseline_at:
            return pd.DataFrame()

        events: list[dict[str, Any]] = []

        def add_rows(table: str, date_col: str, flag_col: str, source: str) -> None:
            rows = conn.execute(
                f"""
                SELECT {date_col} AS event_at,nm_id,COALESCE(supplier_article,'') AS supplier_article,
                       COALESCE({flag_col},0) AS event_flag,COALESCE(raw_json,'') AS raw_json
                  FROM {table}
                 WHERE COALESCE(raw_json,'')<>''
                """
            ).fetchall()
            for row in rows:
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
                srid = str(payload.get("srid") or payload.get("gNumber") or "")
                events.append({
                    "income_id": income_id,
                    "event_at": str(row["event_at"] or ""),
                    "nm_id": int(row["nm_id"] or payload.get("nmId") or 0),
                    "supplier_article": str(row["supplier_article"] or payload.get("supplierArticle") or ""),
                    "source": source,
                    "event_flag": int(row["event_flag"] or 0),
                    "srid": srid,
                })

        add_rows("orders", "order_date", "is_cancel", "order")
        add_rows("sales", "sale_date", "is_return", "sale")
        if not events:
            return pd.DataFrame()

        df = pd.DataFrame(events)
        df["event_dt"] = pd.to_datetime(df["event_at"], errors="coerce")
        df = df[df["event_dt"].notna()].copy()
        if df.empty:
            return pd.DataFrame()
        baseline_dt = pd.to_datetime(baseline_at, errors="coerce")
        if pd.isna(baseline_dt):
            return pd.DataFrame()

        first_seen = df.groupby("income_id", as_index=True)["event_dt"].min()
        candidate_ids = set(first_seen[first_seen > baseline_dt].index.astype(int).tolist())
        if not candidate_ids:
            return pd.DataFrame()
        df = df[df["income_id"].isin(candidate_ids)].copy()

        rows_out: list[dict[str, Any]] = []
        for income_id, group in df.groupby("income_id"):
            articles = sorted({str(x) for x in group["supplier_article"].tolist() if str(x)})
            nm_ids = sorted({int(x) for x in group["nm_id"].tolist() if int(x) > 0})
            orders_ok = group[(group["source"] == "order") & (group["event_flag"] == 0)]
            sales_ok = group[(group["source"] == "sale") & (group["event_flag"] == 0)]
            returns = group[(group["source"] == "sale") & (group["event_flag"] == 1)]

            def unique_units(part: pd.DataFrame) -> int:
                if part.empty:
                    return 0
                srids = [str(x) for x in part["srid"].tolist() if str(x)]
                return len(set(srids)) if srids else len(part)

            rows_out.append({
                "income_id": int(income_id),
                "first_observed_at": group["event_dt"].min().isoformat(),
                "last_observed_at": group["event_dt"].max().isoformat(),
                "sku_count": len(nm_ids),
                "nm_ids": ", ".join(str(x) for x in nm_ids),
                "articles": ", ".join(articles),
                "orders_seen_units": unique_units(orders_ok),
                "sales_seen_units": unique_units(sales_ok),
                "returns_seen_units": unique_units(returns),
                "baseline_snapshot_at": baseline_at,
            })

        return pd.DataFrame(rows_out).sort_values("first_observed_at").reset_index(drop=True)

