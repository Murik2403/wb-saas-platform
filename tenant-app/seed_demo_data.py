"""Seeds the tenant SQLite database with realistic-looking but entirely
fictional data, for the public "demo" tenant shown on the landing page
(https://demo.app.marketshelper.ru/). Never run this against a real
customer's database -- it wipes and replaces every row in every table it
touches.

The product mix is deliberately spread across profit / at-risk / loss-making
so the anomaly-highlighting panel on the Overview page has something real to
show a visitor who has never seen the product before.

Usage (from tenant-app/, with its venv/deps active):
    WB_SAAS_IS_DEMO=1 WB_SAAS_TENANT_SLUG=demo python3 seed_demo_data.py
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from db.core import connect, ensure_financial_schema, init_db


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
    # This file deletes tenant data. Refuse to run unless the fixed demo
    # provisioner explicitly identifies the isolated demo volume.
    if os.environ.get("WB_SAAS_IS_DEMO") != "1" or os.environ.get("WB_SAAS_TENANT_SLUG") != "demo":
        raise RuntimeError("Refusing to reseed: this script is restricted to the isolated demo tenant.")
    random.seed(42)
    init_db()
    ensure_financial_schema()

    with connect() as conn:
        for table in (
            "sales_fifo_events", "finished_goods_fifo_allocations", "material_fifo_consumptions",
            "production_cost_batches", "manufacturing_defects", "manufacturing_piecework",
            "manufacturing_releases", "manufacturing_boms", "manufacturing_equipment",
            "inventory_movements", "material_cost_layers",
            "orders", "sales", "stocks", "ads_daily", "costs", "products_catalog",
            "financial_report", "finished_goods_cost_layers",
            "production_settings", "material_inventory_color", "production_capacity",
            "product_pipeline", "execution_tasks",
            "procurement_payments", "procurement_items", "procurement_orders", "suppliers",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM manual_expenses WHERE note LIKE 'DEMO-FIFO-CASE:%'")
        conn.execute("DELETE FROM app_meta WHERE key IN ('sales_fifo_tracking_initialized','demo_learning_scenario_v1')")

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

    _seed_fifo_learning_scenario()
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO app_meta(key,value,updated_at) VALUES ('demo_fixture_date',?,?)", (TODAY.isoformat(), datetime.now().isoformat(timespec="seconds")))
    print(f"Demo data seeded: {len(PRODUCTS)} products, {DAYS_OF_HISTORY + 1} days of history and one FIFO learning scenario.")


def _seed_fifo_learning_scenario() -> None:
    """Add one real, rolling-date procurement-to-return flow to the demo DB."""
    import pandas as pd
    from db.core import connect
    from db.fifo_sales import initialize_sales_fifo_tracking, process_sales_fifo_events
    from db.inventory_movements import post_dispatches, post_wb_receipts
    from db.manufacturing import post_manufacturing_release, save_bom_version
    from db.manual_expenses import save_manual_expense
    from db.procurement import create_procurement_order, post_procurement_receipt
    from db.production import save_execution_tasks

    nm_id = 100001
    article = "SPICE-ORG-01"
    product = "Органайзер для специй настенный"
    material = "ПВХ-кромка 2мм белая"
    offsets = {"purchase": 32, "receipt": 28, "production": 24, "dispatch": 20, "arrival": 17, "sale": 12, "return": 5}
    days = {key: (TODAY - timedelta(days=value)).isoformat() for key, value in offsets.items()}

    initialize_sales_fifo_tracking()  # old generated rows are history; only this example is FIFO-applied
    order_id = create_procurement_order(
        procurement_type="Сырьё", supplier_name="ООО Кромка+", status="Заказано",
        order_date=days["purchase"], payment_due_date=days["purchase"],
        expected_date=days["receipt"], note="Учебная закупка для сценария FIFO",
        source_key="demo-fifo-learning-procurement-v1",
        items=[{"material_name": material, "quantity": 100, "unit": "м", "supplier_unit_price": 38}],
    )
    with connect() as conn:
        item = conn.execute("SELECT id FROM procurement_items WHERE order_id=? ORDER BY id LIMIT 1", (order_id,)).fetchone()
        payment = conn.execute("SELECT 1 FROM procurement_payments WHERE order_id=? AND status='applied' LIMIT 1", (order_id,)).fetchone()
        if not payment:
            conn.execute(
                "INSERT INTO procurement_payments(order_id,payment_date,amount,method,note,status,created_at) VALUES (?,?,?,?,?,'applied',?)",
                (order_id, days["purchase"], 3800, "Банк", "Учебный платёж демо-сценария", datetime.now().isoformat(timespec="seconds")),
            )
    receipt = post_procurement_receipt(order_id, pd.DataFrame([{"item_id": int(item["id"]), "receive_now": 100}]))
    if receipt.get("errors") or receipt.get("items", 0) != 1:
        raise RuntimeError(f"Demo learning material receipt failed: {receipt}")
    with connect() as conn:
        movement = conn.execute(
            "SELECT id FROM inventory_movements WHERE source_task_key=? AND movement_type='procurement_material_receipt'",
            (f"procurement_item:{int(item['id'])}",),
        ).fetchone()
        if movement:
            conn.execute("UPDATE inventory_movements SET movement_date=? WHERE id=?", (days["receipt"], int(movement["id"])))
            conn.execute("UPDATE material_cost_layers SET source_date=? WHERE source_ref=?", (days["receipt"], f"movement:{int(movement['id'])}"))
        conn.execute("UPDATE procurement_orders SET received_date=? WHERE id=?", (days["receipt"], order_id))

    bom = save_bom_version(
        nm_id, materials=[{"material_name": material, "quantity_per_unit": 0.4, "unit": "м"}],
        packaging_per_unit=12, other_per_unit=3,
        note="Учебный BOM демо: упаковка и прочие расходы подтверждены явно.",
    )
    release = post_manufacturing_release(
        nm_id, quantity=10, production_date=days["production"], source_key="demo-fifo-learning-release-v1",
        bom_version_id=int(bom["id"]), supplier_article=article, product_name=product, chrt_id=nm_id,
    )
    if not release.get("posted"):
        raise RuntimeError(f"Demo learning production release failed: {release}")
    with connect() as conn:
        conn.execute("UPDATE inventory_movements SET movement_date=? WHERE id=?", (days["production"], int(release["movement_id"])))

    save_execution_tasks(pd.DataFrame([{
        "task_key": "demo-fifo-learning-dispatch-v1", "task_type": "dispatch", "task_date": days["dispatch"],
        "stage": "Отгрузка", "nm_id": nm_id, "chrt_id": nm_id, "supplier_article": article, "product_name": product,
        "planned_units": 10, "actual_units": 10, "status": "Отгружено", "route": "Стандартная FBO",
        "dispatch_date": days["dispatch"], "expected_arrival_date": days["arrival"],
        "note": "Учебный FIFO-сценарий демо",
    }]))
    dispatch = post_dispatches(["demo-fifo-learning-dispatch-v1"])
    if dispatch.get("errors") or dispatch.get("posted") != 1:
        raise RuntimeError(f"Demo learning dispatch failed: {dispatch}")
    with connect() as conn:
        conn.execute("UPDATE execution_tasks SET status='Принято WB' WHERE task_key=?", ("demo-fifo-learning-dispatch-v1",))
    arrival = post_wb_receipts(["demo-fifo-learning-dispatch-v1"])
    if arrival.get("errors") or arrival.get("posted") != 1:
        raise RuntimeError(f"Demo learning WB receipt failed: {arrival}")
    with connect() as conn:
        conn.execute("UPDATE inventory_movements SET movement_date=? WHERE source_task_key=? AND movement_type='wb_receipt'", (days["arrival"], "demo-fifo-learning-dispatch-v1"))

    save_manual_expense(
        nm_id=nm_id, article="advertising", amount_rub=120, allocation_basis="total", source="manual",
        period_start=days["sale"], period_end=days["sale"], note="DEMO-FIFO-CASE: учебный рекламный расход",
    )
    with connect() as conn:
        for suffix, day, is_return, sign in (("sale", days["sale"], 0, 1), ("return", days["return"], 1, -1)):
            sale_id = f"demo-fifo-learning-{suffix}-v1"
            raw = '{"srid":"DEMO-FIFO-LEARNING-001","chrtID":100001,"demo":true}'
            price = 890.0
            conn.execute(
                "INSERT INTO sales(id,sale_date,nm_id,supplier_article,brand,subject,warehouse_name,sale_id,total_price,finished_price,price_with_disc,for_pay,is_return,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sale_id, day, nm_id, article, BRAND, "Хранение", WAREHOUSE, sale_id, price, price, price, price*(1-COMMISSION_RATE)*sign, is_return, raw),
            )
            conn.execute(
                "INSERT INTO financial_report(rrd_id,operation_date,nm_id,supplier_article,quantity,retail_amount,retail_price_withdisc_rub,ppvz_for_pay,commission,delivery_rub,storage_fee,operation_name,doc_type_name,ppvz_sales_commission,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (9000001 if is_return == 0 else 9000002, day, nm_id, article, sign, price*sign, price, price*(1-COMMISSION_RATE)*sign, price*COMMISSION_RATE*sign, LOGISTICS_FEE, 0, "Возврат" if is_return else "Продажа", "Продажа", price*COMMISSION_RATE*sign, raw),
            )
    fifo = process_sales_fifo_events()
    if fifo.get("errors") or fifo.get("sales") != 1 or fifo.get("returns") != 1:
        raise RuntimeError(f"Demo learning sale/return FIFO failed: {fifo}")
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_meta(key,value,updated_at) VALUES ('demo_learning_scenario_v1','1',?)",
            (datetime.now().isoformat(timespec="seconds"),),
        )


if __name__ == "__main__":
    main()
