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


def upsert_orders(rows: Iterable[dict[str, Any]]) -> int:
    from .core import _dump, _int, _num, connect
    payload = []
    for row in rows:
        identity = str(row.get("srid") or row.get("odid") or row.get("gNumber") or "")
        if not identity:
            identity = f"{row.get('nmId', 0)}:{row.get('barcode', '')}:{row.get('date', '')}:{hash(_dump(row))}"
        payload.append(
            (
                identity,
                row.get("date"),
                row.get("lastChangeDate"),
                _int(row.get("nmId")),
                row.get("supplierArticle", ""),
                row.get("barcode", ""),
                row.get("brand", ""),
                row.get("subject", ""),
                row.get("warehouseName", ""),
                _num(row.get("totalPrice")),
                _num(row.get("finishedPrice")),
                _num(row.get("priceWithDisc")),
                1 if row.get("isCancel") else 0,
                row.get("cancelDate"),
                _dump(row),
            )
        )
    if not payload:
        return 0
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                order_date=excluded.order_date,
                last_change=excluded.last_change,
                nm_id=excluded.nm_id,
                supplier_article=excluded.supplier_article,
                barcode=excluded.barcode,
                brand=excluded.brand,
                subject=excluded.subject,
                warehouse_name=excluded.warehouse_name,
                total_price=excluded.total_price,
                finished_price=excluded.finished_price,
                price_with_disc=excluded.price_with_disc,
                is_cancel=excluded.is_cancel,
                cancel_date=excluded.cancel_date,
                raw_json=excluded.raw_json
            """,
            payload,
        )
    return len(payload)

def upsert_sales(rows: Iterable[dict[str, Any]]) -> int:
    from .core import _dump, _int, _num, connect
    payload = []
    for row in rows:
        sale_id = str(row.get("saleID") or "")
        identity = sale_id or str(row.get("srid") or row.get("odid") or "")
        if not identity:
            identity = f"{row.get('nmId', 0)}:{row.get('barcode', '')}:{row.get('date', '')}:{hash(_dump(row))}"
        payload.append(
            (
                identity,
                row.get("date"),
                row.get("lastChangeDate"),
                _int(row.get("nmId")),
                row.get("supplierArticle", ""),
                row.get("barcode", ""),
                row.get("brand", ""),
                row.get("subject", ""),
                row.get("warehouseName", ""),
                sale_id,
                _num(row.get("totalPrice")),
                _num(row.get("finishedPrice")),
                _num(row.get("priceWithDisc")),
                _num(row.get("forPay")),
                1 if sale_id.upper().startswith("R") else 0,
                _dump(row),
            )
        )
    if not payload:
        return 0
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                sale_date=excluded.sale_date,
                last_change=excluded.last_change,
                nm_id=excluded.nm_id,
                supplier_article=excluded.supplier_article,
                barcode=excluded.barcode,
                brand=excluded.brand,
                subject=excluded.subject,
                warehouse_name=excluded.warehouse_name,
                sale_id=excluded.sale_id,
                total_price=excluded.total_price,
                finished_price=excluded.finished_price,
                price_with_disc=excluded.price_with_disc,
                for_pay=excluded.for_pay,
                is_return=excluded.is_return,
                raw_json=excluded.raw_json
            """,
            payload,
        )
    return len(payload)

def insert_stocks(rows: Iterable[dict[str, Any]], snapshot_at: str | None = None) -> int:
    from .core import _dump, _int, connect
    snapshot_at = snapshot_at or datetime.now().replace(second=0, microsecond=0).isoformat()
    payload = []
    for row in rows:
        payload.append(
            (
                snapshot_at,
                _int(row.get("nmId")),
                _int(row.get("chrtId")),
                _int(row.get("warehouseId")),
                row.get("warehouseName", ""),
                row.get("regionName", ""),
                _int(row.get("quantity")),
                _int(row.get("inWayToClient")),
                _int(row.get("inWayFromClient")),
                _dump(row),
            )
        )
    if not payload:
        return 0
    with connect() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO stocks VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            payload,
        )
    return len(payload)

def upsert_ads(rows: Iterable[dict[str, Any]]) -> int:
    from .core import _dump, _int, _num, connect
    payload = []
    for row in rows:
        payload.append(
            (
                _int(row.get("campaign_id")),
                str(row.get("day", ""))[:10],
                _int(row.get("nm_id")),
                row.get("product_name", ""),
                _int(row.get("views")),
                _int(row.get("clicks")),
                _num(row.get("spend")),
                _int(row.get("atbs")),
                _int(row.get("orders_count")),
                _int(row.get("canceled")),
                _int(row.get("shks")),
                _num(row.get("ad_revenue")),
                _dump(row.get("raw", row)),
            )
        )
    if not payload:
        return 0
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO ads_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(campaign_id, day, nm_id) DO UPDATE SET
                product_name=excluded.product_name,
                views=excluded.views,
                clicks=excluded.clicks,
                spend=excluded.spend,
                atbs=excluded.atbs,
                orders_count=excluded.orders_count,
                canceled=excluded.canceled,
                shks=excluded.shks,
                ad_revenue=excluded.ad_revenue,
                raw_json=excluded.raw_json
            """,
            payload,
        )
    return len(payload)

def replace_product_catalog(rows: Iterable[dict[str, Any]]) -> int:
    from .core import _dump, _int, connect
    payload = []
    for row in rows:
        nm_id = _int(row.get("nmID") or row.get("nmId"))
        if not nm_id:
            continue
        subject_name = row.get("subjectName") or row.get("object") or row.get("subject") or ""
        payload.append(
            (
                nm_id,
                str(row.get("vendorCode") or row.get("supplierArticle") or ""),
                str(row.get("title") or row.get("name") or ""),
                str(row.get("brand") or ""),
                str(subject_name or ""),
                str(row.get("updatedAt") or row.get("updated_at") or ""),
                _dump(row),
            )
        )
    if not payload:
        return 0
    with connect() as conn:
        conn.execute("DELETE FROM products_catalog")
        conn.executemany(
            """
            INSERT INTO products_catalog(
                nm_id,supplier_article,product_name,brand,subject_name,updated_at,raw_json
            ) VALUES (?,?,?,?,?,?,?)
            """,
            payload,
        )
    return len(payload)

def delete_ads_period(date_from: str, date_to: str) -> None:
    from .core import connect
    with connect() as conn:
        conn.execute(
            "DELETE FROM ads_daily WHERE day >= ? AND day <= ?",
            (str(date_from)[:10], str(date_to)[:10]),
        )

def cleanup_orphan_zero_costs() -> int:
    """Remove empty cost rows that are not tied to any real product data.

    This specifically cleans stale rows left by demo data while retaining costs
    for active, historical or financially referenced products.
    """
    from .core import connect
    with connect() as conn:
        cur = conn.execute(
            """
            DELETE FROM costs
            WHERE COALESCE(cost_per_wb_unit, 0) <= 0
              AND nm_id NOT IN (SELECT nm_id FROM products_catalog)
              AND nm_id NOT IN (SELECT DISTINCT nm_id FROM orders WHERE nm_id > 0)
              AND nm_id NOT IN (SELECT DISTINCT nm_id FROM sales WHERE nm_id > 0)
              AND nm_id NOT IN (SELECT DISTINCT nm_id FROM stocks WHERE nm_id > 0)
              AND nm_id NOT IN (SELECT DISTINCT nm_id FROM financial_report WHERE nm_id > 0)
            """
        )
        return int(cur.rowcount or 0)

def upsert_financial(rows: Iterable[dict[str, Any]]) -> int:
    from .core import _dump, _first, _int, _num, connect, ensure_financial_schema
    ensure_financial_schema()
    payload = []
    for row in rows:
        rrd_id = _int(_first(row, "rrd_id", "rrdId"))
        if not rrd_id:
            continue
        ppvz_sales_commission = _num(_first(row, "ppvz_sales_commission", "ppvzSalesCommission"))
        payload.append({
            "rrd_id": rrd_id,
            "report_id": _int(_first(row, "realizationreport_id", "realizationReportId", "reportId")),
            "operation_date": _first(row, "rr_dt", "rrDate", "create_dt", "createDt", "date_to", "dateTo"),
            "sale_date": _first(row, "sale_dt", "saleDt"),
            "nm_id": _int(_first(row, "nm_id", "nmId")),
            "supplier_article": str(_first(row, "sa_name", "supplierArticle", "vendorCode", default="") or ""),
            "quantity": _int(_first(row, "quantity")),
            "retail_amount": _num(_first(row, "retail_amount", "retailAmount")),
            "retail_price_withdisc_rub": _num(_first(row, "retail_price_withdisc_rub", "retailPriceWithDisc")),
            "ppvz_for_pay": _num(_first(row, "ppvz_for_pay", "forPay")),
            "commission": ppvz_sales_commission,
            "delivery_rub": _num(_first(row, "delivery_rub", "deliveryService")),
            "storage_fee": _num(_first(row, "storage_fee", "paidStorage")),
            "deduction": _num(_first(row, "deduction")),
            "penalty": _num(_first(row, "penalty")),
            "acceptance": _num(_first(row, "acceptance", "paidAcceptance")),
            "operation_name": str(_first(row, "supplier_oper_name", "sellerOperName", default="") or ""),
            "raw_json": _dump(row),
            "doc_type_name": str(_first(row, "doc_type_name", "docTypeName", default="") or ""),
            "ppvz_sales_commission": ppvz_sales_commission,
            "ppvz_reward": _num(_first(row, "ppvz_reward", "ppvzReward")),
            "ppvz_vw": _num(_first(row, "ppvz_vw", "vw")),
            "ppvz_vw_nds": _num(_first(row, "ppvz_vw_nds", "vwNds")),
            "acquiring_fee": _num(_first(row, "acquiring_fee", "acquiringFee")),
            "additional_payment": _num(_first(row, "additional_payment", "additionalPayment")),
            "rebill_logistic_cost": _num(_first(row, "rebill_logistic_cost", "rebillLogisticCost")),
            "delivery_amount": _int(_first(row, "delivery_amount", "deliveryAmount")),
            "return_amount": _int(_first(row, "return_amount", "returnAmount")),
        })
    if not payload:
        return 0

    columns = list(payload[0].keys())
    placeholders = ",".join("?" for _ in columns)
    update_columns = [c for c in columns if c != "rrd_id"]
    sql = f"""
        INSERT INTO financial_report ({','.join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(rrd_id) DO UPDATE SET
        {','.join(f'{c}=excluded.{c}' for c in update_columns)}
    """
    values = [tuple(row[c] for c in columns) for row in payload]
    with connect() as conn:
        conn.executemany(sql, values)
    return len(payload)

def read_financial() -> pd.DataFrame:
    from .core import connect, ensure_financial_schema
    ensure_financial_schema()
    with connect() as conn:
        return pd.read_sql_query("SELECT * FROM financial_report", conn)
