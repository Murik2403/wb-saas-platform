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
    # `margin` is now the reconciled net profit after COGS, advertising and
    # every accounting expense. WB commission is already included in
    # ppvz_for_pay, the authoritative amount payable to the seller.
    "Чистая прибыль": "margin",
    "Чистая маржинальность, %": "margin_pct",
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

        # The legacy products frame only had a pre-WB margin estimate. Overlay
        # the accounting-backed product economics so the redesigned screen
        # uses the same full-expense result as Finance. Keep the base rows for
        # stock/velocity fields, but leave margin unavailable when a SKU cannot
        # be reconciled rather than presenting a misleading pre-WB amount.
        financial_products = getattr(data, "financial_products", pd.DataFrame())
        if financial_products is not None and not financial_products.empty and "Артикул WB" in financial_products.columns:
            base_ids = pd.to_numeric(products_view.get("Артикул WB"), errors="coerce")
            financial_view = financial_products.copy()
            financial_view["_nm_id"] = pd.to_numeric(financial_view["Артикул WB"], errors="coerce")
            financial_view = financial_view.dropna(subset=["_nm_id"]).drop_duplicates("_nm_id").set_index("_nm_id")
            products_view["_nm_id"] = base_ids
            products_view["Чистая прибыль"] = products_view["_nm_id"].map(financial_view.get("Чистая прибыль", pd.Series(dtype=float)))
            products_view["Чистая маржинальность, %"] = products_view["_nm_id"].map(financial_view.get("Чистая маржинальность, %", pd.Series(dtype=float)))
            products_view = products_view.drop(columns=["_nm_id"])
        else:
            products_view["Чистая прибыль"] = pd.NA
            products_view["Чистая маржинальность, %"] = pd.NA

    return {
        "period": period,
        "period_label": period_label,
        "start": json_safe(start),
        "end": json_safe(end),
        "products": _records(products_view, PRODUCT_COLUMNS),
    }
