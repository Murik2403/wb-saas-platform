from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from db import init_db, insert_stocks, upsert_ads, upsert_orders, upsert_sales

PRODUCTS = [
    (391054484, "HOME-DIY-BEIGE", "Бежевый"),
    (391054485, "HOME-DIY-BROWN", "Коричневый"),
    (391054486, "HOME-DIY-BLUE", "Синий"),
    (391054487, "HOME-DIY-BURGUNDY", "Бордовый"),
    (391054488, "HOME-DIY-PISTACHIO", "Фисташковый"),
]


def generate_demo(days: int = 75) -> None:
    init_db()
    random.seed(42)
    orders = []
    sales = []
    ads = []
    start = date.today() - timedelta(days=days - 1)
    order_counter = 0
    sale_counter = 0

    for offset in range(days):
        day = start + timedelta(days=offset)
        trend = 1 + offset / max(days, 1) * 0.25
        weekend = 1.18 if day.weekday() >= 5 else 1.0
        for nm_id, article, color in PRODUCTS:
            base = {391054484: 14, 391054485: 11, 391054486: 8, 391054487: 7, 391054488: 9}[nm_id]
            qty = max(0, int(random.gauss(base * trend * weekend, 2.5)))
            price = random.choice([1290, 1390, 1490])
            canceled = 0
            sold = 0
            for i in range(qty):
                order_counter += 1
                is_cancel = random.random() < 0.08
                canceled += int(is_cancel)
                ts = datetime.combine(day, datetime.min.time()) + timedelta(
                    hours=random.randint(8, 23), minutes=random.randint(0, 59)
                )
                orders.append(
                    {
                        "srid": f"DEMO-O-{order_counter}",
                        "date": ts.isoformat(),
                        "lastChangeDate": ts.isoformat(),
                        "nmId": nm_id,
                        "supplierArticle": article,
                        "barcode": f"DEMO{nm_id}",
                        "brand": "HOME DIY",
                        "subject": "Плейсматы",
                        "warehouseName": random.choice(["Коледино", "Электросталь", "Казань"]),
                        "totalPrice": price,
                        "finishedPrice": price * random.uniform(0.88, 0.98),
                        "priceWithDisc": price * random.uniform(0.88, 0.98),
                        "isCancel": is_cancel,
                        "cancelDate": ts.isoformat() if is_cancel else None,
                        "demo": True,
                    }
                )
                if not is_cancel and random.random() < 0.88:
                    sold += 1
                    sale_counter += 1
                    sale_ts = ts + timedelta(days=random.randint(1, 5))
                    sales.append(
                        {
                            "saleID": f"DEMO-S-{sale_counter}",
                            "date": sale_ts.isoformat(),
                            "lastChangeDate": sale_ts.isoformat(),
                            "nmId": nm_id,
                            "supplierArticle": article,
                            "barcode": f"DEMO{nm_id}",
                            "brand": "HOME DIY",
                            "subject": "Плейсматы",
                            "warehouseName": random.choice(["Коледино", "Электросталь", "Казань"]),
                            "totalPrice": price,
                            "finishedPrice": price * random.uniform(0.86, 0.96),
                            "priceWithDisc": price * random.uniform(0.86, 0.96),
                            "forPay": price * random.uniform(0.48, 0.58),
                            "demo": True,
                        }
                    )
            spend = max(0, qty * random.uniform(70, 125))
            ads.append(
                {
                    "campaign_id": 900000 + nm_id % 100,
                    "day": day.isoformat(),
                    "nm_id": nm_id,
                    "product_name": color,
                    "views": qty * random.randint(90, 150),
                    "clicks": qty * random.randint(4, 8),
                    "spend": spend,
                    "atbs": max(0, qty - canceled),
                    "orders_count": max(0, int(qty * random.uniform(0.45, 0.72))),
                    "canceled": canceled,
                    "shks": sold,
                    "ad_revenue": sold * price * random.uniform(0.45, 0.75),
                    "raw": {"demo": True},
                }
            )

    upsert_orders(orders)
    upsert_sales(sales)
    upsert_ads(ads)

    stocks = []
    for idx, (nm_id, article, color) in enumerate(PRODUCTS):
        total = [420, 265, 130, 65, 210][idx]
        for warehouse_id, warehouse in [(101, "Коледино"), (102, "Электросталь"), (103, "Казань")]:
            qty = max(0, int(total / 3 + random.randint(-20, 20)))
            stocks.append(
                {
                    "nmId": nm_id,
                    "chrtId": nm_id + 1000,
                    "warehouseId": warehouse_id,
                    "warehouseName": warehouse,
                    "regionName": "Центральный" if warehouse_id != 103 else "Приволжский",
                    "quantity": qty,
                    "inWayToClient": random.randint(0, 12),
                    "inWayFromClient": random.randint(0, 5),
                    "demo": True,
                }
            )
    insert_stocks(stocks, datetime.now().replace(second=0, microsecond=0).isoformat())
