"""Pure computation for the "Товары" (Products) screen.

The Products counterpart to today_service.py / overview_service.py. The
Streamlit page (pages/products.py) is a single read-only view of
`data.products` -- the per-article economics DataFrame calculations.py already
builds -- so there is no math to port here beyond selecting the same columns
and handing them to the frontend under stable English keys. Nothing in this
module touches Streamlit; pages/products.py stays live and unchanged.

Like Обзор, this screen is period-scoped: in Streamlit the period selector
lives in app.py above the page, so the API takes the same `period` query param
and reuses overview_service.resolve_window() rather than re-deriving the date
math. See memory/project_marketshelper_frontend_migration.md.
"""

from __future__ import annotations

import pandas as pd

from today_service import _records, json_safe

# data.products' own column order (calculations.py's product_rows dict), mapped
# to the API's English keys. The DataFrame keeps its Cyrillic names -- those are
# what pages/products.py's column_config still addresses.
PRODUCT_COLUMNS: dict[str, str] = {
    "Артикул WB": "nm_id",
    "Артикул продавца": "article",
    "Товар": "product",
    "Бренд": "brand",
    "Заказы": "orders",
    "Продажи": "sales",
    "Выручка": "revenue",
    "Выкуп, %": "buyout",
    "Реклама": "ad_spend",
    "ДРР, %": "drr",
    "Остаток": "stock",
    "Продаж/день": "daily_sales",
    "Запас, дней": "days_of_stock",
    "Себестоимость ед.": "unit_cost",
    "Маржа до комиссий WB": "margin",
}


def compute_products(ctx: dict) -> dict:
    data = ctx["data"]
    period = ctx["period"]
    period_label = ctx.get("period_label", period)
    start, end = ctx["start"], ctx["end"]

    products_view = pd.DataFrame()
    if not data.products.empty:
        # Explicit column order, tolerant of a column calculations.py hasn't
        # produced for this window (an empty ads join, say) rather than raising.
        present = [c for c in PRODUCT_COLUMNS if c in data.products.columns]
        products_view = data.products[present].copy()

    return {
        "period": period,
        "period_label": period_label,
        "start": json_safe(start),
        "end": json_safe(end),
        "products": _records(products_view, PRODUCT_COLUMNS),
    }
