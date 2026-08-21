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


PROCUREMENT_STATUSES = (
    "Запланировано", "Заказано", "Частично оплачено", "Оплачено", "В пути",
    "Частично получено", "Получено", "Отменено",
)

PAYMENT_METHODS = ("Банк", "Карта", "Наличные", "Зачёт", "Другое")

def _procurement_date_text(value: Any) -> str | None:
    from .production import _movement_date_text
    return _movement_date_text(value)

def save_supplier(
    supplier_id: int | None,
    name: str,
    contact_person: str = "",
    phone: str = "",
    messenger: str = "",
    email: str = "",
    country: str = "",
    default_currency: str = "RUB",
    payment_terms_days: int = 3,
    lead_time_days: int = 7,
    active: bool = True,
    note: str = "",
) -> int:
    from .core import connect
    name = str(name or "").strip()
    if not name:
        raise ValueError("Укажите название поставщика.")
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        if supplier_id:
            existing = conn.execute("SELECT id FROM suppliers WHERE id=?", (int(supplier_id),)).fetchone()
            if existing is None:
                raise ValueError("Поставщик не найден.")
            conn.execute(
                """
                UPDATE suppliers
                   SET name=?,contact_person=?,phone=?,messenger=?,email=?,country=?,
                       default_currency=?,payment_terms_days=?,lead_time_days=?,active=?,note=?,updated_at=?
                 WHERE id=?
                """,
                (name, str(contact_person or ""), str(phone or ""), str(messenger or ""),
                 str(email or ""), str(country or ""), str(default_currency or "RUB"),
                 max(0, int(payment_terms_days or 0)), max(0, int(lead_time_days or 0)),
                 1 if active else 0, str(note or ""), now, int(supplier_id)),
            )
            conn.execute("UPDATE procurement_orders SET supplier_name=? WHERE supplier_id=?", (name, int(supplier_id)))
            return int(supplier_id)
        row = conn.execute("SELECT id FROM suppliers WHERE lower(name)=lower(?)", (name,)).fetchone()
        if row:
            supplier_id = int(row["id"])
            conn.execute(
                """
                UPDATE suppliers
                   SET contact_person=?,phone=?,messenger=?,email=?,country=?,default_currency=?,
                       payment_terms_days=?,lead_time_days=?,active=?,note=?,updated_at=?
                 WHERE id=?
                """,
                (str(contact_person or ""), str(phone or ""), str(messenger or ""), str(email or ""),
                 str(country or ""), str(default_currency or "RUB"), max(0, int(payment_terms_days or 0)),
                 max(0, int(lead_time_days or 0)), 1 if active else 0, str(note or ""), now, supplier_id),
            )
            return supplier_id
        cur = conn.execute(
            """
            INSERT INTO suppliers(
                name,contact_person,phone,messenger,email,country,default_currency,
                payment_terms_days,lead_time_days,active,note,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (name, str(contact_person or ""), str(phone or ""), str(messenger or ""), str(email or ""),
             str(country or ""), str(default_currency or "RUB"), max(0, int(payment_terms_days or 0)),
             max(0, int(lead_time_days or 0)), 1 if active else 0, str(note or ""), now, now),
        )
        return int(cur.lastrowid)

def read_suppliers(active_only: bool = False) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        where = "WHERE s.active=1" if active_only else ""
        return pd.read_sql_query(
            f"""
            SELECT s.*,
                   COALESCE(o.order_count,0) AS order_count,
                   COALESCE(o.total_amount,0) AS total_amount,
                   COALESCE(o.received_orders,0) AS received_orders,
                   o.last_order_date,
                   COALESCE(p.paid_amount,0) AS paid_amount
              FROM suppliers s
              LEFT JOIN (
                    SELECT supplier_id,COUNT(*) AS order_count,
                           SUM(COALESCE((SELECT SUM(i.quantity*i.unit_price) FROM procurement_items i WHERE i.order_id=po.id),0)) AS total_amount,
                           SUM(CASE WHEN status='Получено' THEN 1 ELSE 0 END) AS received_orders,
                           MAX(order_date) AS last_order_date
                      FROM procurement_orders po
                     WHERE supplier_id IS NOT NULL AND status<>'Отменено'
                     GROUP BY supplier_id
              ) o ON o.supplier_id=s.id
              LEFT JOIN (
                    SELECT po.supplier_id,SUM(pp.amount) AS paid_amount
                      FROM procurement_payments pp
                      JOIN procurement_orders po ON po.id=pp.order_id
                     WHERE pp.status='applied' AND po.supplier_id IS NOT NULL
                     GROUP BY po.supplier_id
              ) p ON p.supplier_id=s.id
              {where}
             ORDER BY s.active DESC,s.name
            """,
            conn,
        )

def supplier_defaults(supplier_id: int | None) -> dict[str, Any]:
    from .core import connect
    if not supplier_id:
        return {}
    with connect() as conn:
        row = conn.execute("SELECT * FROM suppliers WHERE id=?", (int(supplier_id),)).fetchone()
        return dict(row) if row else {}

def _ensure_supplier_conn(conn: sqlite3.Connection, supplier_name: str) -> int | None:
    name = str(supplier_name or "").strip()
    if not name:
        return None
    row = conn.execute("SELECT id FROM suppliers WHERE lower(name)=lower(?)", (name,)).fetchone()
    if row:
        return int(row["id"])
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO suppliers(name,created_at,updated_at) VALUES (?,?,?)",
        (name, now, now),
    )
    return int(cur.lastrowid)

def _next_procurement_number(conn: sqlite3.Connection) -> str:
    prefix = datetime.now().strftime("ЗАК-%Y%m%d-")
    row = conn.execute(
        "SELECT order_number FROM procurement_orders WHERE order_number LIKE ? ORDER BY id DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    seq = 1
    if row:
        try:
            seq = int(str(row["order_number"]).rsplit("-", 1)[-1]) + 1
        except Exception:
            seq = 1
    return f"{prefix}{seq:03d}"

def _landed_unit_cost(
    supplier_unit_price: Any,
    exchange_rate: Any,
    delivery_unit_foreign: Any = 0,
    extra_unit_rub: Any = 0,
    legacy_unit_price: Any = 0,
) -> float:
    supplier_price = max(0.0, float(supplier_unit_price or 0))
    delivery_foreign = max(0.0, float(delivery_unit_foreign or 0))
    extra_rub = max(0.0, float(extra_unit_rub or 0))
    rate = max(0.000001, float(exchange_rate or 1))
    if supplier_price > 0 or delivery_foreign > 0 or extra_rub > 0:
        return (supplier_price + delivery_foreign) * rate + extra_rub
    return max(0.0, float(legacy_unit_price or 0))

def create_procurement_order(
    procurement_type: str,
    supplier_name: str,
    status: str,
    order_date: Any,
    payment_due_date: Any,
    expected_date: Any,
    note: str,
    items: pd.DataFrame | Iterable[dict[str, Any]],
    source_key: str | None = None,
    currency: str = "RUB",
    exchange_rate: float = 1.0,
) -> int:
    from .core import _int, connect
    procurement_type = str(procurement_type or "").strip()
    if procurement_type not in {"Сырьё", "Товар"}:
        raise ValueError("Тип закупки должен быть «Сырьё» или «Товар».")
    status = str(status or "Запланировано").strip()
    if status not in PROCUREMENT_STATUSES:
        raise ValueError("Неизвестный статус закупки.")
    frame = items.copy() if isinstance(items, pd.DataFrame) else pd.DataFrame(list(items or []))
    if frame.empty:
        raise ValueError("Добавьте хотя бы одну позицию закупки.")
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        if source_key:
            existing = conn.execute(
                "SELECT id FROM procurement_orders WHERE source_key=?", (str(source_key),)
            ).fetchone()
            if existing:
                return int(existing["id"])
        order_number = _next_procurement_number(conn)
        supplier_id = _ensure_supplier_conn(conn, supplier_name)
        cur = conn.execute(
            """
            INSERT INTO procurement_orders(
                order_number,procurement_type,supplier_name,supplier_id,status,order_date,payment_due_date,
                paid_date,expected_date,received_date,currency,exchange_rate,note,source_key,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                order_number, procurement_type, str(supplier_name or ""), supplier_id, status,
                _procurement_date_text(order_date) or datetime.now().date().isoformat(),
                _procurement_date_text(payment_due_date),
                datetime.now().date().isoformat() if status in {"Оплачено", "В пути", "Частично получено", "Получено"} else None,
                _procurement_date_text(expected_date), None, str(currency or "RUB").upper(),
                max(0.000001, float(exchange_rate or 1)),
                str(note or ""), str(source_key or "") or None, now, now,
            ),
        )
        order_id = int(cur.lastrowid)
        rows: list[tuple[Any, ...]] = []
        for _, row in frame.iterrows():
            quantity = max(0.0, float(row.get("quantity", 0) or 0))
            if quantity <= 0:
                continue
            if procurement_type == "Сырьё":
                material_name = str(row.get("material_name", "") or "").strip()
                if not material_name:
                    continue
                # Any unit is accepted -- only "рулон" triggers roll-length conversion math
                # elsewhere (create_procurement_order's receipt/costing logic, _apply_material_delta).
                # Every other value (м, кг, л, шт, упаковка, or a tenant's own free text) is treated
                # as a direct quantity, so this must not clamp unrecognized units back to "рулон".
                unit = str(row.get("unit", "рулон") or "рулон").strip() or "рулон"
                roll_length = max(0.1, float(row.get("roll_length", 25.5) or 25.5))
                item_rate = max(0.000001, float(row.get("exchange_rate", exchange_rate) or exchange_rate or 1))
                supplier_price = max(0.0, float(row.get("supplier_unit_price", 0) or 0))
                delivery_foreign = max(0.0, float(row.get("delivery_unit_foreign", 0) or 0))
                extra_rub = max(0.0, float(row.get("extra_unit_rub", 0) or 0))
                legacy_price = max(0.0, float(row.get("unit_price", 0) or 0))
                landed_price = _landed_unit_cost(supplier_price, item_rate, delivery_foreign, extra_rub, legacy_price)
                if supplier_price <= 0 and delivery_foreign <= 0 and extra_rub <= 0 and legacy_price > 0:
                    supplier_price, item_rate = legacy_price, 1.0
                rows.append((
                    order_id, "Сырьё", material_name.casefold(), material_name, 0, "", "",
                    quantity, unit, roll_length, supplier_price, item_rate, delivery_foreign, extra_rub, landed_price,
                    0.0, 0.0, str(row.get("note", "") or ""), now, now,
                ))
            else:
                nm_id = _int(row.get("nm_id"))
                supplier_article = str(row.get("supplier_article", "") or "").strip()
                product_name = str(row.get("product_name", "") or "").strip()
                if nm_id <= 0 and not supplier_article:
                    continue
                item_rate = max(0.000001, float(row.get("exchange_rate", exchange_rate) or exchange_rate or 1))
                supplier_price = max(0.0, float(row.get("supplier_unit_price", 0) or 0))
                delivery_foreign = max(0.0, float(row.get("delivery_unit_foreign", 0) or 0))
                extra_rub = max(0.0, float(row.get("extra_unit_rub", 0) or 0))
                legacy_price = max(0.0, float(row.get("unit_price", 0) or 0))
                landed_price = _landed_unit_cost(supplier_price, item_rate, delivery_foreign, extra_rub, legacy_price)
                if supplier_price <= 0 and delivery_foreign <= 0 and extra_rub <= 0 and legacy_price > 0:
                    supplier_price, item_rate = legacy_price, 1.0
                rows.append((
                    order_id, "Товар", "", "", nm_id, supplier_article, product_name,
                    quantity, "шт", 0.0, supplier_price, item_rate, delivery_foreign, extra_rub, landed_price,
                    0.0, 0.0, str(row.get("note", "") or ""), now, now,
                ))
        if not rows:
            raise ValueError("Не найдено корректных позиций с количеством больше нуля.")
        conn.executemany(
            """
            INSERT INTO procurement_items(
                order_id,item_type,material_key,material_name,nm_id,supplier_article,product_name,
                quantity,unit,roll_length,supplier_unit_price,exchange_rate,delivery_unit_foreign,extra_unit_rub,
                unit_price,received_quantity,posted_quantity,note,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        return order_id

def read_procurement_orders() -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            WITH item_totals AS (
                SELECT order_id,COUNT(*) AS item_count,
                       COALESCE(SUM(quantity),0) AS quantity_total,
                       COALESCE(SUM(posted_quantity),0) AS posted_total,
                       COALESCE(SUM(quantity*COALESCE(supplier_unit_price,0)*COALESCE(exchange_rate,1)),0) AS supplier_amount_rub,
                       COALESCE(SUM(quantity*COALESCE(delivery_unit_foreign,0)*COALESCE(exchange_rate,1)),0) AS delivery_amount_rub,
                       COALESCE(SUM(quantity*COALESCE(extra_unit_rub,0)),0) AS extra_amount_rub,
                       COALESCE(SUM(quantity*unit_price),0) AS total_amount
                  FROM procurement_items GROUP BY order_id
            ), payment_totals AS (
                SELECT order_id,COUNT(*) AS payment_count,
                       COALESCE(SUM(amount),0) AS paid_amount,
                       MAX(payment_date) AS last_payment_date
                  FROM procurement_payments
                 WHERE status='applied'
                 GROUP BY order_id
            )
            SELECT o.*,s.contact_person AS supplier_contact,s.phone AS supplier_phone,
                   s.messenger AS supplier_messenger,s.email AS supplier_email,
                   s.payment_terms_days,s.lead_time_days,
                   COALESCE(i.item_count,0) AS item_count,
                   COALESCE(i.quantity_total,0) AS quantity_total,
                   COALESCE(i.posted_total,0) AS posted_total,
                   COALESCE(i.supplier_amount_rub,0) AS supplier_amount_rub,
                   COALESCE(i.delivery_amount_rub,0) AS delivery_amount_rub,
                   COALESCE(i.extra_amount_rub,0) AS extra_amount_rub,
                   COALESCE(i.total_amount,0) AS total_amount,
                   COALESCE(p.payment_count,0) AS payment_count,
                   COALESCE(p.paid_amount,0) AS paid_amount,
                   MAX(0,COALESCE(i.total_amount,0)-COALESCE(p.paid_amount,0)) AS outstanding_amount,
                   p.last_payment_date
              FROM procurement_orders o
              LEFT JOIN item_totals i ON i.order_id=o.id
              LEFT JOIN payment_totals p ON p.order_id=o.id
              LEFT JOIN suppliers s ON s.id=o.supplier_id
             ORDER BY CASE o.status
                        WHEN 'Запланировано' THEN 1 WHEN 'Заказано' THEN 2
                        WHEN 'Частично оплачено' THEN 3 WHEN 'Оплачено' THEN 4
                        WHEN 'В пути' THEN 5 WHEN 'Частично получено' THEN 6
                        WHEN 'Получено' THEN 7 ELSE 8 END,
                      COALESCE(o.expected_date,'9999-12-31'), o.id DESC
            """,
            conn,
        )

def read_procurement_items(order_id: int | None = None) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        if order_id is None:
            return pd.read_sql_query(
                "SELECT * FROM procurement_items ORDER BY order_id,id", conn
            )
        return pd.read_sql_query(
            "SELECT * FROM procurement_items WHERE order_id=? ORDER BY id",
            conn, params=(int(order_id),),
        )

def update_procurement_items(df: pd.DataFrame) -> None:
    from .core import _int, connect
    required = {"id", "quantity"}
    if df is None or df.empty or not required.issubset(df.columns):
        raise ValueError("Не хватает данных позиций закупки.")
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        for _, row in df.iterrows():
            item_id = _int(row.get("id"))
            if item_id <= 0:
                continue
            current = conn.execute("SELECT * FROM procurement_items WHERE id=?", (item_id,)).fetchone()
            if current is None:
                continue
            posted = float(current["posted_quantity"] or 0)
            quantity = max(posted, float(row.get("quantity", 0) or 0))
            roll_length = max(0.1, float(row.get("roll_length", current["roll_length"] or 25.5) or 25.5))
            supplier_price = max(0.0, float(row.get("supplier_unit_price", current["supplier_unit_price"] or 0) or 0))
            exchange_rate = max(0.000001, float(row.get("exchange_rate", current["exchange_rate"] or 1) or 1))
            delivery_foreign = max(0.0, float(row.get("delivery_unit_foreign", current["delivery_unit_foreign"] or 0) or 0))
            extra_rub = max(0.0, float(row.get("extra_unit_rub", current["extra_unit_rub"] or 0) or 0))
            legacy_price = max(0.0, float(row.get("unit_price", current["unit_price"] or 0) or 0))
            unit_price = _landed_unit_cost(supplier_price, exchange_rate, delivery_foreign, extra_rub, legacy_price)
            note = str(row.get("note", current["note"] or "") or "")
            conn.execute(
                """
                UPDATE procurement_items
                   SET quantity=?,supplier_unit_price=?,exchange_rate=?,delivery_unit_foreign=?,extra_unit_rub=?,
                       unit_price=?,roll_length=?,note=?,updated_at=?
                 WHERE id=?
                """,
                (quantity, supplier_price, exchange_rate, delivery_foreign, extra_rub,
                 unit_price, roll_length, note, now, item_id),
            )

def update_procurement_order(
    order_id: int,
    supplier_name: str,
    status: str,
    order_date: Any,
    payment_due_date: Any,
    expected_date: Any,
    note: str,
    currency: str | None = None,
    exchange_rate: float | None = None,
) -> None:
    from .core import connect
    status = str(status or "Запланировано").strip()
    if status not in PROCUREMENT_STATUSES:
        raise ValueError("Неизвестный статус закупки.")
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        row = conn.execute("SELECT * FROM procurement_orders WHERE id=?", (int(order_id),)).fetchone()
        if row is None:
            raise ValueError("Закупка не найдена.")
        supplier_id = _ensure_supplier_conn(conn, supplier_name)
        received_date = row["received_date"]
        if status == "Получено" and not received_date:
            received_date = datetime.now().date().isoformat()
        if status != "Получено":
            posted = float(conn.execute(
                "SELECT COALESCE(SUM(posted_quantity),0) AS n FROM procurement_items WHERE order_id=?",
                (int(order_id),),
            ).fetchone()["n"] or 0)
            if posted <= 0:
                received_date = None
        conn.execute(
            """
            UPDATE procurement_orders
               SET supplier_name=?,supplier_id=?,status=?,order_date=?,payment_due_date=?,
                   expected_date=?,received_date=?,currency=?,exchange_rate=?,note=?,updated_at=?
             WHERE id=?
            """,
            (str(supplier_name or ""), supplier_id, status,
             _procurement_date_text(order_date), _procurement_date_text(payment_due_date),
             _procurement_date_text(expected_date), received_date,
             str(currency or row["currency"] or "RUB").upper(),
             max(0.000001, float(exchange_rate if exchange_rate is not None else row["exchange_rate"] or 1)),
             str(note or ""), now, int(order_id)),
        )
        if exchange_rate is not None:
            new_rate = max(0.000001, float(exchange_rate or 1))
            conn.execute(
                """
                UPDATE procurement_items
                   SET exchange_rate=?,
                       unit_price=(COALESCE(supplier_unit_price,0)+COALESCE(delivery_unit_foreign,0))*?+COALESCE(extra_unit_rub,0),
                       updated_at=?
                 WHERE order_id=?
                """,
                (new_rate, new_rate, now, int(order_id)),
            )
        if status == "Оплачено":
            totals = conn.execute(
                """
                SELECT COALESCE((SELECT SUM(quantity*unit_price) FROM procurement_items WHERE order_id=?),0) AS total,
                       COALESCE((SELECT SUM(amount) FROM procurement_payments WHERE order_id=? AND status='applied'),0) AS paid
                """,
                (int(order_id), int(order_id)),
            ).fetchone()
            remaining = max(0.0, float(totals["total"] or 0) - float(totals["paid"] or 0))
            if remaining > 0:
                conn.execute(
                    """
                    INSERT INTO procurement_payments(order_id,payment_date,amount,method,note,status,created_at)
                    VALUES (?,?,?,?,?,'applied',?)
                    """,
                    (int(order_id), datetime.now().date().isoformat(), remaining,
                     "Статус заявки", "Автоматически при переводе в статус «Оплачено»", now),
                )
        _refresh_procurement_payment_status(conn, int(order_id), preserve_logistics=True)

def _procurement_amounts_conn(conn: sqlite3.Connection, order_id: int) -> tuple[float, float]:
    row = conn.execute(
        """
        SELECT COALESCE((SELECT SUM(quantity*unit_price) FROM procurement_items WHERE order_id=?),0) AS total,
               COALESCE((SELECT SUM(amount) FROM procurement_payments WHERE order_id=? AND status='applied'),0) AS paid
        """,
        (int(order_id), int(order_id)),
    ).fetchone()
    return float(row["total"] or 0), float(row["paid"] or 0)

def _refresh_procurement_payment_status(conn: sqlite3.Connection, order_id: int, preserve_logistics: bool = True) -> None:
    order = conn.execute("SELECT status FROM procurement_orders WHERE id=?", (int(order_id),)).fetchone()
    if order is None:
        return
    current = str(order["status"] or "Запланировано")
    total, paid = _procurement_amounts_conn(conn, int(order_id))
    fully_paid = total > 0 and paid + 0.005 >= total
    if preserve_logistics and current in {"В пути", "Частично получено", "Получено", "Отменено"}:
        new_status = current
    elif fully_paid:
        new_status = "Оплачено"
    elif paid > 0:
        new_status = "Частично оплачено"
    elif current in {"Частично оплачено", "Оплачено"}:
        new_status = "Заказано"
    else:
        new_status = current
    paid_date = datetime.now().date().isoformat() if fully_paid else None
    conn.execute(
        "UPDATE procurement_orders SET status=?,paid_date=?,updated_at=? WHERE id=?",
        (new_status, paid_date, datetime.now().isoformat(timespec="seconds"), int(order_id)),
    )

def record_procurement_payment(
    order_id: int,
    amount: float,
    payment_date: Any,
    method: str = "Банк",
    note: str = "",
) -> int:
    from .core import connect
    amount = round(float(amount or 0), 2)
    if amount <= 0:
        raise ValueError("Сумма оплаты должна быть больше нуля.")
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        order = conn.execute("SELECT id FROM procurement_orders WHERE id=?", (int(order_id),)).fetchone()
        if order is None:
            raise ValueError("Закупка не найдена.")
        total, paid = _procurement_amounts_conn(conn, int(order_id))
        outstanding = max(0.0, total - paid)
        if total > 0 and amount > outstanding + 0.01:
            raise ValueError(f"Сумма превышает остаток к оплате: {outstanding:.2f} ₽.")
        cur = conn.execute(
            """
            INSERT INTO procurement_payments(order_id,payment_date,amount,method,note,status,created_at)
            VALUES (?,?,?,?,?,'applied',?)
            """,
            (int(order_id), _procurement_date_text(payment_date) or datetime.now().date().isoformat(),
             amount, str(method or "Банк"), str(note or ""), now),
        )
        _refresh_procurement_payment_status(conn, int(order_id), preserve_logistics=True)
        return int(cur.lastrowid)

def read_procurement_payments(order_id: int | None = None) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        if order_id is None:
            return pd.read_sql_query(
                """
                SELECT p.*,o.order_number,o.supplier_name,o.procurement_type
                  FROM procurement_payments p
                  JOIN procurement_orders o ON o.id=p.order_id
                 ORDER BY p.payment_date DESC,p.id DESC
                """,
                conn,
            )
        return pd.read_sql_query(
            """
            SELECT p.*,o.order_number,o.supplier_name,o.procurement_type
              FROM procurement_payments p
              JOIN procurement_orders o ON o.id=p.order_id
             WHERE p.order_id=? ORDER BY p.payment_date DESC,p.id DESC
            """,
            conn, params=(int(order_id),),
        )

def undo_procurement_payment(payment_id: int) -> dict[str, Any]:
    from .core import connect
    result = {"ok": False, "message": "Платёж не найден."}
    with connect() as conn:
        row = conn.execute("SELECT * FROM procurement_payments WHERE id=?", (int(payment_id),)).fetchone()
        if row is None:
            return result
        if str(row["status"] or "") != "applied":
            return {"ok": False, "message": "Платёж уже отменён."}
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "UPDATE procurement_payments SET status='reversed',reversed_at=? WHERE id=?",
            (now, int(payment_id)),
        )
        _refresh_procurement_payment_status(conn, int(row["order_id"]), preserve_logistics=True)
        return {"ok": True, "message": "Платёж отменён, остаток к оплате пересчитан."}

def delete_procurement_order(order_id: int) -> None:
    from .core import connect
    with connect() as conn:
        posted = conn.execute(
            "SELECT COALESCE(SUM(posted_quantity),0) AS n FROM procurement_items WHERE order_id=?",
            (int(order_id),),
        ).fetchone()["n"]
        if float(posted or 0) > 0:
            raise ValueError("Нельзя удалить закупку с проведённой приёмкой. Сначала отмените движение в журнале.")
        conn.execute("DELETE FROM procurement_payments WHERE order_id=?", (int(order_id),))
        conn.execute("DELETE FROM procurement_items WHERE order_id=?", (int(order_id),))
        conn.execute("DELETE FROM procurement_orders WHERE id=?", (int(order_id),))

def _refresh_procurement_order_status(conn: sqlite3.Connection, order_id: int) -> None:
    order = conn.execute("SELECT * FROM procurement_orders WHERE id=?", (int(order_id),)).fetchone()
    if order is None or str(order["status"] or "") == "Отменено":
        return
    totals = conn.execute(
        """
        SELECT COALESCE(SUM(quantity),0) AS qty, COALESCE(SUM(posted_quantity),0) AS posted
          FROM procurement_items WHERE order_id=?
        """,
        (int(order_id),),
    ).fetchone()
    qty = float(totals["qty"] or 0)
    posted = float(totals["posted"] or 0)
    now = datetime.now().isoformat(timespec="seconds")
    if qty > 0 and posted >= qty - 0.0001:
        status = "Получено"
        received_date = datetime.now().date().isoformat()
    elif posted > 0:
        status = "Частично получено"
        received_date = None
    else:
        current = str(order["status"] or "Запланировано")
        if current in {"Получено", "Частично получено"}:
            status = "В пути" if order["paid_date"] else "Заказано"
        else:
            status = current
        received_date = None
    conn.execute(
        "UPDATE procurement_orders SET status=?,received_date=?,updated_at=? WHERE id=?",
        (status, received_date, now, int(order_id)),
    )

def post_procurement_receipt(order_id: int, receipt_df: pd.DataFrame) -> dict[str, Any]:
    from .core import _int, connect
    from .fifo_finished_goods import _create_finished_goods_layer
    from .fifo_materials import _create_material_layer
    from .production import _apply_material_delta, _apply_pipeline_delta, _material_key, _next_event_key
    result: dict[str, Any] = {"posted": 0, "items": 0, "errors": [], "materials_m": 0.0, "products": 0}
    if receipt_df is None or receipt_df.empty or "item_id" not in receipt_df.columns:
        result["errors"].append("Нет позиций для приёмки.")
        return result
    receive_map: dict[int, float] = {}
    for _, row in receipt_df.iterrows():
        item_id = _int(row.get("item_id"))
        qty = max(0.0, float(row.get("receive_now", 0) or 0))
        if item_id > 0 and qty > 0:
            receive_map[item_id] = qty
    if not receive_map:
        result["errors"].append("Укажите фактически полученное количество.")
        return result
    with connect() as conn:
        order = conn.execute("SELECT * FROM procurement_orders WHERE id=?", (int(order_id),)).fetchone()
        if order is None:
            result["errors"].append("Закупка не найдена.")
            return result
        if str(order["status"] or "") == "Отменено":
            result["errors"].append("Отменённую закупку нельзя оприходовать.")
            return result
        for item_id, receive_now in receive_map.items():
            item = conn.execute(
                "SELECT * FROM procurement_items WHERE id=? AND order_id=?",
                (int(item_id), int(order_id)),
            ).fetchone()
            if item is None:
                result["errors"].append(f"Позиция #{item_id} не найдена.")
                continue
            remaining = max(0.0, float(item["quantity"] or 0) - float(item["posted_quantity"] or 0))
            qty = min(float(receive_now), remaining)
            if qty <= 0:
                continue
            source_key = f"procurement_item:{int(item_id)}"
            now = datetime.now().isoformat(timespec="seconds")
            try:
                conn.execute("SAVEPOINT procurement_receipt_item")
                material_delta = 0.0
                ready_delta = 0
                movement_type = "procurement_product_receipt"
                if str(item["item_type"] or "") == "Сырьё":
                    material_name = str(item["material_name"] or "").strip()
                    key = _material_key(item["material_key"] or material_name)
                    roll_length = max(0.1, float(item["roll_length"] or 25.5))
                    existing = conn.execute(
                        "SELECT * FROM material_inventory_color WHERE material_key=?", (key,)
                    ).fetchone()
                    if existing is None:
                        conn.execute(
                            """
                            INSERT INTO material_inventory_color(
                                material_key,material_name,balance_known,full_rolls,partial_meters,roll_length,note,updated_at
                            ) VALUES (?,?,?,?,?,?,?,?)
                            """,
                            (key, material_name, 1, 0, 0.0, roll_length, "Создано при приёмке закупки", now),
                        )
                    elif int(existing["balance_known"] or 0) != 1:
                        conn.execute(
                            "UPDATE material_inventory_color SET balance_known=1,roll_length=?,updated_at=? WHERE material_key=?",
                            (roll_length, now, key),
                        )
                    material_delta = qty * roll_length if str(item["unit"] or "") == "рулон" else qty
                    _apply_material_delta(conn, key, material_name, material_delta)
                    movement_type = "procurement_material_receipt"
                    result["materials_m"] += material_delta
                else:
                    ready_delta = int(round(qty))
                    if ready_delta <= 0:
                        raise ValueError("Количество товара должно быть целым и больше нуля.")
                    _apply_pipeline_delta(
                        conn, int(item["nm_id"] or 0), str(item["supplier_article"] or ""), ready_delta, 0
                    )
                    result["products"] += ready_delta
                event_key = _next_event_key(conn, movement_type, source_key)
                movement_cur = conn.execute(
                    """
                    INSERT INTO inventory_movements(
                        event_key,movement_type,nm_id,supplier_article,product_name,quantity,
                        ready_delta,inbound_delta,route,movement_date,expected_arrival_date,
                        source_task_key,reference_movement_id,status,note,created_at,reversed_at,
                        material_key,material_name,material_delta,procurement_order_id,procurement_item_id,
                        procurement_quantity,material_cost_rub,unit_cost_rub
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event_key, movement_type, int(item["nm_id"] or 0),
                        str(item["supplier_article"] or ""), str(item["product_name"] or ""),
                        ready_delta if ready_delta else int(round(qty)), ready_delta, 0, "Закупка",
                        datetime.now().date().isoformat(), None, source_key, None, "applied",
                        f"Приёмка закупки {order['order_number']}", now, None,
                        str(item["material_key"] or ""), str(item["material_name"] or ""), material_delta,
                        int(order_id), int(item_id), float(qty), 0.0, 0.0,
                    ),
                )
                movement_id = int(movement_cur.lastrowid)
                landed_unit = _landed_unit_cost(
                    item["supplier_unit_price"], item["exchange_rate"],
                    item["delivery_unit_foreign"], item["extra_unit_rub"], item["unit_price"],
                )
                if movement_type == "procurement_product_receipt" and ready_delta > 0:
                    layer_id = _create_finished_goods_layer(
                        conn, int(item["nm_id"] or 0), str(item["supplier_article"] or ""),
                        str(item["product_name"] or ""), "procurement_product", f"movement:{movement_id}",
                        datetime.now().date().isoformat(), ready_delta, landed_unit, "ready",
                        f"Закупка {order['order_number']}; позиция #{int(item_id)}; фактическая полная стоимость.",
                    )
                    receipt_amount = round(ready_delta * landed_unit, 2)
                    conn.execute(
                        "UPDATE inventory_movements SET goods_cost_rub=?,goods_unit_cost_rub=?,note=? WHERE id=?",
                        (receipt_amount, landed_unit,
                         f"Приёмка закупки {order['order_number']}; слой готовой продукции #{layer_id}: {ready_delta} ед. по {landed_unit:.2f} ₽",
                         movement_id),
                    )
                if movement_type == "procurement_material_receipt" and material_delta > 0:
                    unit_text = str(item["unit"] or "").strip().casefold()
                    if unit_text == "рулон":
                        rate_rub_m = landed_unit / max(0.1, float(item["roll_length"] or 25.5))
                    else:
                        rate_rub_m = landed_unit
                    layer_id = _create_material_layer(
                        conn, str(item["material_key"] or item["material_name"]),
                        str(item["material_name"] or ""), "procurement", f"movement:{movement_id}",
                        datetime.now().date().isoformat(), material_delta, rate_rub_m,
                        f"Закупка {order['order_number']}; позиция #{int(item_id)}; фактическая полная стоимость.",
                    )
                    receipt_amount = round(material_delta * rate_rub_m, 2)
                    conn.execute(
                        "UPDATE inventory_movements SET material_cost_rub=?,unit_cost_rub=?,note=? WHERE id=?",
                        (receipt_amount, rate_rub_m,
                         f"Приёмка закупки {order['order_number']}; FIFO-слой #{layer_id}: {material_delta:.3f} м по {rate_rub_m:.2f} ₽/м",
                         movement_id),
                    )
                conn.execute(
                    """
                    UPDATE procurement_items
                       SET received_quantity=received_quantity+?,posted_quantity=posted_quantity+?,updated_at=?
                     WHERE id=?
                    """,
                    (qty, qty, now, int(item_id)),
                )
                conn.execute("RELEASE SAVEPOINT procurement_receipt_item")
                result["posted"] += qty
                result["items"] += 1
            except Exception as exc:
                try:
                    conn.execute("ROLLBACK TO SAVEPOINT procurement_receipt_item")
                    conn.execute("RELEASE SAVEPOINT procurement_receipt_item")
                except Exception:
                    pass
                result["errors"].append(f"{item['material_name'] or item['supplier_article']}: {exc}")
        _refresh_procurement_order_status(conn, int(order_id))
    return result

def read_procurement_movements(limit: int = 300) -> pd.DataFrame:
    from .core import connect
    with connect() as conn:
        return pd.read_sql_query(
            """
            SELECT * FROM inventory_movements
             WHERE movement_type IN ('procurement_material_receipt','procurement_product_receipt')
                OR procurement_order_id IS NOT NULL
             ORDER BY id DESC LIMIT ?
            """,
            conn, params=(max(1, int(limit)),),
        )
