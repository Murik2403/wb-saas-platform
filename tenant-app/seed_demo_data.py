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

import json
import random
from datetime import date, timedelta

from db.core import connect, ensure_financial_schema, init_db

random.seed(42)  # deterministic output -- redeploying the demo shouldn't reshuffle it

TODAY = date.today()
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
        for table in ("orders", "sales", "stocks", "ads_daily", "costs", "products_catalog", "financial_report"):
            conn.execute(f"DELETE FROM {table}")

        for nm_id, article, name, subject, price, cost, _, _, _, _ in PRODUCTS:
            conn.execute(
                "INSERT INTO products_catalog (nm_id, supplier_article, product_name, brand, subject_name, updated_at, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (nm_id, article, name, BRAND, subject, TODAY.isoformat(), "{}"),
            )
            conn.execute(
                "INSERT INTO costs (nm_id, supplier_article, cost_per_wb_unit, updated_at) VALUES (?, ?, ?, ?)",
                (nm_id, article, cost, TODAY.isoformat()),
            )
            conn.execute(
                "INSERT INTO stocks (snapshot_at, nm_id, chrt_id, warehouse_id, warehouse_name, region_name, quantity, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (TODAY.isoformat(), nm_id, nm_id, 1, WAREHOUSE, REGION, PRODUCTS[[p[0] for p in PRODUCTS].index(nm_id)][9], "{}"),
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
