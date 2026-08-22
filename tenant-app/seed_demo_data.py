"""Seeds the tenant SQLite database with realistic-looking but entirely
fictional data, for the public "demo" tenant shown on the landing page
(https://demo.app.marketshelper.ru/). Never run this against a real
customer's database -- it wipes and replaces every row in every table it
touches.

The product mix is deliberately spread across profit / at-risk / loss-making
so the anomaly-highlighting panel on the Overview page has something real to
show a visitor who has never seen the product before.

Usage (from tenant-app/, with its venv/deps active):
    python3 seed_demo_data.py
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from db.core import connect, ensure_financial_schema, init_db

random.seed(42)  # deterministic output -- redeploying the demo shouldn't reshuffle it

# The dashboard's "Сегодня" page buckets rows by Moscow-time date (see
# app.py's today_msk); seeding with the container's own (usually UTC) date
# can land "today" a day off from what the UI asks for, making the Сегодня
# tab show all zeros right after a redeploy.
TODAY = datetime.now(ZoneInfo("Europe/Moscow")).date()
DAYS_OF_HISTORY = 60
COMMISSION_RATE = 0.17
LOGISTICS_FEE = 55.0

# nm_id, supplier_article, product_name, subject, price, cost_per_unit,
# avg_daily_sales, ad_spend_per_day, return_rate, stock_qty
PRODUCTS = [
    (100001, "SPICE-ORG-01", "Органайзер для специй настенный", "Хранение", 890, 220, 6.0, 180, 0.04, 340),
    (100002, "HOOK-SET-12", "Крючки самоклеящиеся, набор 12 шт", "Крепёж", 350, 60, 9.0, 90, 0.03, 610),
    (100003, "SHELF-WALL-40", "Полка настенная 40 см дуб", "Мебель", 1590, 640, 3.5, 220, 0.05, 120),
    (100004, "BOX-STOR-L", "Коробка для хранения, большая", "Хранение", 690, 260, 4.0, 60, 0.04, 200),
    (100005, "LAMP-LED-STRIP", "Светодиодная лента с пультом 5м", "Освещение", 990, 410, 3.0, 260, 0.09, 85),
    (100006, "MAT-DOOR-01", "Придверный коврик резиновый", "Для дома", 590, 250, 2.2, 40, 0.05, 260),
    (100007, "HANGER-SET-8", "Плечики бархатные, набор 8 шт", "Гардероб", 450, 140, 2.8, 30, 0.03, 400),
    (100008, "TOWEL-HOLDER", "Держатель для полотенец настенный", "Ванная", 420, 180, 1.6, 20, 0.04, 150),
    (100009, "CABLE-ORG-05", "Органайзер для проводов, 5 шт", "Электрика", 320, 210, 2.0, 140, 0.10, 70),
    (100010, "MIRROR-ROUND", "Зеркало настенное круглое 40 см", "Декор", 1290, 690, 1.2, 260, 0.12, 30),
    (100011, "CANDLE-SET-3", "Свечи ароматические, набор 3 шт", "Декор", 590, 310, 1.0, 190, 0.14, 55),
    (100012, "RUG-SMALL-01", "Коврик придверный маленький", "Для дома", 350, 260, 0.8, 210, 0.22, 12),
    (100013, "CLOCK-WALL-30", "Часы настенные 30 см", "Декор", 890, 640, 0.4, 240, 0.18, 8),
]
BRAND = "HomeDIY"
WAREHOUSE = "Коледино"
REGION = "Московская область"


def poisson(mean: float) -> int:
    if mean <= 0:
        return 0
    # Knuth's algorithm -- good enough for small means, no numpy dependency needed.
    import math

    l = math.exp(-mean)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= random.random()
        if p <= l:
            return k - 1


def jitter(price: float, spread: float = 0.05) -> float:
    return round(price * (1 + random.uniform(-spread, spread)), 2)


def main() -> None:
    init_db()
    ensure_financial_schema()

    with connect() as conn:
        for table in (
            "orders", "sales", "stocks", "ads_daily", "costs", "products_catalog",
            "financial_report", "finished_goods_cost_layers",
            "production_settings", "material_inventory_color", "production_capacity",
            "product_pipeline", "execution_tasks",
            "procurement_payments", "procurement_items", "procurement_orders", "suppliers",
        ):
            conn.execute(f"DELETE FROM {table}")

        now_iso = TODAY.isoformat()
        # Ready/in-transit units per product (ready_units, inbound_units, inbound
        # lead days) -- defined here so both product_pipeline (further below) and
        # the FIFO cost layer seeded per product agree on the same ready/inbound
        # counts. Disagreeing between the two is exactly what the FIFO
        # reconciliation on Остатки/Сегодня is designed to catch, so seeding them
        # inconsistently would trigger a false "Расхождения по N артикулам" alert.
        pipeline = {
            100001: (45, 120, 5),
            100002: (80, 0, 0),
            100003: (15, 30, 7),
            100005: (0, 50, 4),
            100010: (0, 20, 6),
        }
        for nm_id, article, name, subject, price, cost, _, _, _, stock_qty in PRODUCTS:
            conn.execute(
                "INSERT INTO products_catalog (nm_id, supplier_article, product_name, brand, subject_name, updated_at, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (nm_id, article, name, BRAND, subject, now_iso, "{}"),
            )
            conn.execute(
                "INSERT INTO costs (nm_id, supplier_article, cost_per_wb_unit, updated_at) VALUES (?, ?, ?, ?)",
                (nm_id, article, cost, now_iso),
            )
            conn.execute(
                "INSERT INTO stocks (snapshot_at, nm_id, chrt_id, warehouse_id, warehouse_name, region_name, quantity, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now_iso, nm_id, nm_id, 1, WAREHOUSE, REGION, stock_qty, "{}"),
            )
            # The WB-warehouse FIFO reconciliation (see db/wb_fifo_reconciliation.py)
            # flags every article as a discrepancy if physical stock/pipeline units
            # have no matching FIFO cost layer at all -- seed one per product,
            # including its ready/inbound units, so the demo's Сегодня page
            # doesn't open on a false "расхождения по N артикулам" alert.
            ready_units, inbound_units, _lead = pipeline.get(nm_id, (0, 0, 0))
            total_units = stock_qty + ready_units + inbound_units
            conn.execute(
                "INSERT INTO finished_goods_cost_layers (nm_id, supplier_article, product_name, source_type, "
                "source_ref, source_date, original_units, ready_units, inbound_units, wb_units, unit_cost_rub, "
                "original_amount_rub, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'opening', ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (nm_id, article, name, f"demo-seed-{nm_id}", now_iso, total_units, ready_units, inbound_units,
                 stock_qty, cost, total_units * cost, now_iso, now_iso),
            )

        # ---- Production: mark 3 products as own manufacturing, seed their
        # shared material's stock, a produced/in-transit pipeline snapshot,
        # and one in-progress shift -- so the Сегодня page's "Смена" and
        # "Готово / в пути" widgets show something instead of "0/0". Current
        # stock already covers each product's target_days, so this
        # deliberately does not trigger any new "Требует внимания" signal.
        MATERIAL_NAME = "ПВХ-кромка 2мм белая"
        material_key = MATERIAL_NAME.strip().casefold()
        own_production = {100001: 0.4, 100002: 0.15, 100003: 1.2}
        for nm_id, rate in own_production.items():
            article = next(p[1] for p in PRODUCTS if p[0] == nm_id)
            conn.execute(
                "INSERT INTO production_settings (nm_id, supplier_article, enabled, material_per_unit, "
                "target_days, min_batch, pack_size, material_name) VALUES (?, ?, 1, ?, 21, 10, 4, ?)",
                (nm_id, article, rate, MATERIAL_NAME),
            )
        conn.execute(
            "INSERT INTO material_inventory_color (material_key, material_name, balance_known, full_rolls, "
            "partial_meters, roll_length, unit, tracking_mode, updated_at) VALUES (?, ?, 1, 12, 8.5, 100, 'м', 'packaged', ?)",
            (material_key, MATERIAL_NAME, now_iso),
        )
        conn.execute(
            "INSERT INTO production_capacity (id, capacity_known, pieces_per_day, workdays, horizon_days, "
            "fulfillment_lead_days, emergency_cover_days, expedited_fbo_lead_days) "
            "VALUES (1, 1, 120, '0,1,2,3,4,5', 14, 5, 10, 3)"
        )
        for nm_id, (ready, inbound, lead) in pipeline.items():
            article = next(p[1] for p in PRODUCTS if p[0] == nm_id)
            inbound_date = (TODAY + timedelta(days=lead)).isoformat() if inbound else ""
            conn.execute(
                "INSERT INTO product_pipeline (nm_id, supplier_article, local_known, ready_units, "
                "inbound_known, inbound_units, inbound_date, updated_at) VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
                (nm_id, article, ready, 1 if inbound else 0, inbound, inbound_date, now_iso),
            )
        conn.execute(
            "INSERT INTO execution_tasks (task_key, task_type, task_date, stage, nm_id, supplier_article, "
            "product_name, planned_units, actual_units, status, updated_at) "
            "VALUES ('demo-shift-1', 'production', ?, 'Изготовление', 100001, 'SPICE-ORG-01', "
            "'Органайзер для специй настенный', 40, 25, 'В работе', ?)",
            (now_iso, now_iso),
        )

        # ---- Procurement: one settled historical order plus one active,
        # already-fully-paid order in transit -- shows a real-looking supply
        # pipeline without tripping the overdue/payment-due alerts (which
        # only fire when money or a delivery date is actually late).
        conn.execute(
            "INSERT INTO suppliers (name, contact_person, phone, country, default_currency, "
            "payment_terms_days, lead_time_days, active, created_at, updated_at) "
            "VALUES ('ООО Кромка+', 'Ирина', '+7 900 000-00-00', 'Россия', 'RUB', 7, 10, 1, ?, ?)",
            (now_iso, now_iso),
        )
        supplier_id = conn.execute("SELECT id FROM suppliers WHERE name='ООО Кромка+'").fetchone()[0]

        conn.execute(
            "INSERT INTO procurement_orders (order_number, procurement_type, supplier_name, supplier_id, "
            "status, order_date, expected_date, received_date, currency, exchange_rate, created_at, updated_at) "
            "VALUES ('DEMO-001', 'Сырьё', 'ООО Кромка+', ?, 'Получено', ?, ?, ?, 'RUB', 1, ?, ?)",
            (supplier_id, (TODAY - timedelta(days=30)).isoformat(), (TODAY - timedelta(days=23)).isoformat(),
             (TODAY - timedelta(days=22)).isoformat(), now_iso, now_iso),
        )
        order1_id = conn.execute("SELECT id FROM procurement_orders WHERE order_number='DEMO-001'").fetchone()[0]
        conn.execute(
            "INSERT INTO procurement_items (order_id, item_type, material_key, material_name, quantity, unit, "
            "roll_length, supplier_unit_price, exchange_rate, unit_price, received_quantity, posted_quantity, "
            "created_at, updated_at) VALUES (?, 'material', ?, ?, 400, 'м', 100, 42, 1, 42, 400, 400, ?, ?)",
            (order1_id, material_key, MATERIAL_NAME, now_iso, now_iso),
        )
        conn.execute(
            "INSERT INTO procurement_payments (order_id, payment_date, amount, method, status, created_at) "
            "VALUES (?, ?, 16800, 'Банк', 'applied', ?)",
            (order1_id, (TODAY - timedelta(days=25)).isoformat(), now_iso),
        )

        conn.execute(
            "INSERT INTO procurement_orders (order_number, procurement_type, supplier_name, supplier_id, "
            "status, order_date, payment_due_date, expected_date, currency, exchange_rate, created_at, updated_at) "
            "VALUES ('DEMO-002', 'Сырьё', 'ООО Кромка+', ?, 'В пути', ?, ?, ?, 'RUB', 1, ?, ?)",
            (supplier_id, (TODAY - timedelta(days=3)).isoformat(), (TODAY + timedelta(days=2)).isoformat(),
             (TODAY + timedelta(days=5)).isoformat(), now_iso, now_iso),
        )
        order2_id = conn.execute("SELECT id FROM procurement_orders WHERE order_number='DEMO-002'").fetchone()[0]
        conn.execute(
            "INSERT INTO procurement_items (order_id, item_type, material_key, material_name, quantity, unit, "
            "roll_length, supplier_unit_price, exchange_rate, unit_price, created_at, updated_at) "
            "VALUES (?, 'material', ?, ?, 500, 'м', 100, 45, 1, 45, ?, ?)",
            (order2_id, material_key, MATERIAL_NAME, now_iso, now_iso),
        )
        conn.execute(
            "INSERT INTO procurement_payments (order_id, payment_date, amount, method, status, created_at) "
            "VALUES (?, ?, 22500, 'Банк', 'applied', ?)",
            (order2_id, (TODAY - timedelta(days=2)).isoformat(), now_iso),
        )

        rrd_id = 1
        campaign_id = 900001
        for day_offset in range(DAYS_OF_HISTORY, -1, -1):
            day = TODAY - timedelta(days=day_offset)
            for nm_id, article, name, subject, price, cost, avg_sales, ad_spend, return_rate, _ in PRODUCTS:
                orders_n = poisson(avg_sales * 1.15)
                sales_n = poisson(avg_sales)
                returns_n = poisson(avg_sales * return_rate)

                for i in range(orders_n):
                    order_price = jitter(price)
                    is_cancel = 1 if random.random() < 0.02 else 0
                    conn.execute(
                        "INSERT INTO orders (id, order_date, nm_id, supplier_article, brand, subject, warehouse_name, "
                        "total_price, finished_price, price_with_disc, is_cancel, raw_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (f"o-{nm_id}-{day.isoformat()}-{i}", day.isoformat(), nm_id, article, BRAND, subject,
                         WAREHOUSE, order_price, order_price, order_price, is_cancel, "{}"),
                    )

                for i in range(sales_n + returns_n):
                    is_return = 1 if i >= sales_n else 0
                    sale_price = jitter(price)
                    conn.execute(
                        "INSERT INTO sales (id, sale_date, nm_id, supplier_article, brand, subject, warehouse_name, "
                        "sale_id, total_price, finished_price, price_with_disc, for_pay, is_return, raw_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (f"s-{nm_id}-{day.isoformat()}-{i}", day.isoformat(), nm_id, article, BRAND, subject,
                         WAREHOUSE, f"s-{nm_id}-{day.isoformat()}-{i}", sale_price, sale_price, sale_price,
                         sale_price * (1 - COMMISSION_RATE), is_return, "{}"),
                    )

                    sign = -1 if is_return else 1
                    for_pay = sale_price * (1 - COMMISSION_RATE) * sign
                    commission = sale_price * COMMISSION_RATE * sign
                    conn.execute(
                        "INSERT INTO financial_report (rrd_id, operation_date, nm_id, supplier_article, quantity, "
                        "retail_amount, retail_price_withdisc_rub, ppvz_for_pay, commission, delivery_rub, "
                        "storage_fee, operation_name, doc_type_name, ppvz_sales_commission, raw_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (rrd_id, day.isoformat(), nm_id, article, 1, sale_price * sign, sale_price, for_pay,
                         commission, LOGISTICS_FEE, 6.0 if not is_return else 0.0,
                         "Возврат" if is_return else "Продажа", "Продажа", commission, "{}"),
                    )
                    rrd_id += 1

                if ad_spend > 0:
                    spend_today = max(0.0, jitter(ad_spend, spread=0.2))
                    views = int(spend_today * random.uniform(15, 25))
                    clicks = int(views * random.uniform(0.02, 0.05))
                    conn.execute(
                        "INSERT INTO ads_daily (campaign_id, day, nm_id, product_name, views, clicks, spend, "
                        "orders_count, ad_revenue, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (campaign_id, day.isoformat(), nm_id, name, views, clicks, spend_today,
                         orders_n, sales_n * price * 0.4, "{}"),
                    )

    print(f"Demo data seeded: {len(PRODUCTS)} products, {DAYS_OF_HISTORY + 1} days of history.")


if __name__ == "__main__":
    main()
