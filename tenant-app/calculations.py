from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from db import read_table, read_financial


@dataclass
class DashboardData:
    kpi: dict[str, float]
    daily: pd.DataFrame
    products: pd.DataFrame
    ads: pd.DataFrame
    stocks: pd.DataFrame
    alerts: pd.DataFrame
    financial: dict[str, float]
    financial_products: pd.DataFrame
    financial_unallocated: pd.DataFrame
    hourly: pd.DataFrame


def _money_series(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    # WB fills several money fields asynchronously. Use the preferred field
    # per row, but fall back to the next candidate when that specific row is
    # still zero instead of discarding its amount for the whole period.
    result = pd.Series(0.0, index=df.index, dtype=float)
    for name in candidates:
        if name not in df.columns:
            continue
        values = pd.to_numeric(df[name], errors="coerce").fillna(0.0)
        missing = result.abs() < 0.000001
        result = result.where(~missing, values)
    return result


def _prepare_dates(df: pd.DataFrame, column: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out[column] = pd.to_datetime(out[column], errors="coerce")
    out = out.dropna(subset=[column])
    out["day"] = out[column].dt.date
    return out



def _contains_any_text(series: pd.Series, needles: tuple[str, ...]) -> pd.Series:
    text = series.fillna("").astype(str).str.casefold()
    result = pd.Series(False, index=series.index)
    for needle in needles:
        result = result | text.str.contains(needle.casefold(), regex=False)
    return result


def _risk_reason(row: pd.Series) -> str:
    sales = float(row.get("sale_units", 0) or 0)
    profit = float(row.get("allocated_profit", 0) or 0)
    margin = float(row.get("allocated_margin_pct", 0) or 0)
    if sales <= 1:
        return "0–1 продажа: недостаточно данных"
    if profit >= 0 and margin >= 10:
        return "Экономика стабильна"

    return_rate = float(row.get("return_rate_pct", 0) or 0)
    ad_share = float(row.get("ad_share_pct", 0) or 0)
    wb_share = float(row.get("wb_share_pct", 0) or 0)
    cost_share = float(row.get("cost_share_pct", 0) or 0)
    factors = {
        "Высокая доля возвратов": return_rate / 15 if return_rate > 0 else 0,
        "Высокие расходы на рекламу": ad_share / 20 if ad_share > 0 else 0,
        "Высокие расходы WB и логистика": wb_share / 35 if wb_share > 0 else 0,
        "Высокая себестоимость": cost_share / 45 if cost_share > 0 else 0,
    }
    reason, severity = max(factors.items(), key=lambda item: item[1])
    if severity >= 1:
        return reason
    return "Низкая цена продажи / недостаточная маржа"


def _recommended_action(row: pd.Series) -> str:
    status = str(row.get("status", "") or "")
    sales = float(row.get("sale_units", 0) or 0)
    profit = float(row.get("allocated_profit", 0) or 0)
    margin = float(row.get("allocated_margin_pct", 0) or 0)
    ad_share = float(row.get("ad_share_pct", 0) or 0)
    return_rate = float(row.get("return_rate_pct", 0) or 0)
    wb_share = float(row.get("wb_share_pct", 0) or 0)
    cost_share = float(row.get("cost_share_pct", 0) or 0)
    days_left = row.get("days_left", np.nan)
    try:
        days_left = float(days_left)
    except (TypeError, ValueError):
        days_left = np.nan

    if sales <= 1 or status == "Недостаточно данных":
        return "Собрать больше данных; не масштабировать"
    if profit < 0:
        if ad_share >= 20:
            return "Снизить ставки или остановить рекламу; затем пересчитать экономику"
        if return_rate >= 15:
            return "Проверить карточку, качество и причины возвратов"
        if wb_share >= 35:
            return "Поднять цену или снизить логистические расходы"
        if cost_share >= 45:
            return "Снизить себестоимость или поднять цену"
        return "Остановить масштабирование; проверить цену и расходы"
    if margin < 10 or status == "Низкая маржа":
        if ad_share >= 15:
            return "Снизить рекламные расходы и поднять маржу"
        return "Проверить повышение цены; производить минимальной партией"

    if pd.notna(days_left):
        if days_left < 7:
            return "Срочно пополнить запас; рекламу пока не увеличивать"
        if days_left < 14:
            return "Поставить в ближайший план производства"
        if days_left > 60:
            return "Не производить; аккуратно масштабировать продажи"
    if status == "Лидер":
        return "Масштабировать рекламу и поддерживать целевой запас"
    return "Поддерживать продажи и контролировать запас"

def build_dashboard(start: date, end: date) -> DashboardData:
    orders = _prepare_dates(read_table("orders"), "order_date")
    sales = _prepare_dates(read_table("sales"), "sale_date")
    ads_all = _prepare_dates(read_table("ads_daily"), "day")
    stocks = read_table("stocks")
    costs = read_table("costs")
    catalog = read_table("products_catalog")
    financial_df = read_financial()

    if not orders.empty:
        orders = orders[(orders["day"] >= start) & (orders["day"] <= end)]
        orders["order_amount"] = _money_series(
            orders, ["finished_price", "price_with_disc", "total_price"]
        )
    if not sales.empty:
        sales = sales[(sales["day"] >= start) & (sales["day"] <= end)]
        # /supplier/sales returns sale amounts as positive values and return
        # amounts as negative values. Normalize the magnitude first and apply
        # the operation sign exactly once. price_with_disc is the closest
        # operational analogue of the WB portal "Выкупы" amount; finished_price
        # can include WB-funded buyer discounts and therefore is not comparable.
        sales["sale_amount"] = _money_series(
            sales, ["price_with_disc", "finished_price", "total_price"]
        ).abs()
        sales["net_qty"] = np.where(sales["is_return"].fillna(0).astype(int) == 1, -1, 1)
        sales["net_revenue"] = sales["sale_amount"] * sales["net_qty"]
    if not ads_all.empty:
        ads_all = ads_all[(ads_all["day"] >= start) & (ads_all["day"] <= end)]

    # v2.3 stores one authoritative campaign/day row with nm_id=0 and
    # separate product allocation rows. Overall KPIs must use only the former.
    ads_total = pd.DataFrame(columns=ads_all.columns) if ads_all.empty else ads_all[
        pd.to_numeric(ads_all["nm_id"], errors="coerce").fillna(0).astype(int) == 0
    ].copy()
    ads_products = pd.DataFrame(columns=ads_all.columns) if ads_all.empty else ads_all[
        pd.to_numeric(ads_all["nm_id"], errors="coerce").fillna(0).astype(int) > 0
    ].copy()
    # Backward-compatible fallback until the first v2.3 resynchronization.
    ads_kpi = ads_total if not ads_total.empty else ads_all

    valid_orders = orders[orders["is_cancel"].fillna(0).astype(int) == 0] if not orders.empty else orders
    orders_count = float(len(valid_orders))
    canceled_count = float(orders["is_cancel"].sum()) if not orders.empty else 0.0
    order_amount = float(valid_orders["order_amount"].sum()) if not valid_orders.empty else 0.0
    sales_count = float((sales["is_return"].fillna(0).astype(int) == 0).sum()) if not sales.empty else 0.0
    returns_count = float((sales["is_return"].fillna(0).astype(int) == 1).sum()) if not sales.empty else 0.0
    net_sales_count = sales_count - returns_count
    revenue = float(sales["net_revenue"].sum()) if not sales.empty else 0.0
    operational_last_change = ""
    if not sales.empty and "last_change" in sales.columns:
        latest_operational = pd.to_datetime(sales["last_change"], errors="coerce").max()
        if pd.notna(latest_operational):
            operational_last_change = latest_operational.strftime("%d.%m.%Y %H:%M")
    ad_spend = float(pd.to_numeric(ads_kpi.get("spend", 0), errors="coerce").fillna(0).sum()) if not ads_kpi.empty else 0.0
    ad_revenue = float(pd.to_numeric(ads_kpi.get("ad_revenue", 0), errors="coerce").fillna(0).sum()) if not ads_kpi.empty else 0.0
    ad_views = float(pd.to_numeric(ads_kpi.get("views", 0), errors="coerce").fillna(0).sum()) if not ads_kpi.empty else 0.0
    ad_clicks = float(pd.to_numeric(ads_kpi.get("clicks", 0), errors="coerce").fillna(0).sum()) if not ads_kpi.empty else 0.0

    latest_stock = pd.DataFrame()
    stock_total = 0.0
    if not stocks.empty:
        stocks["snapshot_at"] = pd.to_datetime(stocks["snapshot_at"], errors="coerce")
        latest_time = stocks["snapshot_at"].max()
        latest_stock = stocks[stocks["snapshot_at"] == latest_time].copy()
        stock_total = float(pd.to_numeric(latest_stock["quantity"], errors="coerce").fillna(0).sum())

    buyout = sales_count / orders_count * 100 if orders_count else 0.0
    drr = ad_spend / revenue * 100 if revenue else 0.0
    ad_drr = ad_spend / ad_revenue * 100 if ad_revenue else 0.0

    kpi = {
        "orders": orders_count,
        "order_amount": order_amount,
        "sales": sales_count,
        "returns": returns_count,
        "net_sales": net_sales_count,
        "revenue": revenue,
        "buyout": buyout,
        "canceled": canceled_count,
        "ad_spend": ad_spend,
        "drr": drr,
        "ad_drr": ad_drr,
        "ad_revenue": ad_revenue,
        "ad_views": ad_views,
        "ad_clicks": ad_clicks,
        "ad_ctr": ad_clicks / ad_views * 100 if ad_views else 0.0,
        "stock": stock_total,
    }

    date_index = pd.date_range(start, end, freq="D")
    daily = pd.DataFrame({"day": date_index.date})
    if not valid_orders.empty:
        group = valid_orders.groupby("day", as_index=False).agg(
            orders=("id", "count"), order_amount=("order_amount", "sum")
        )
        daily = daily.merge(group, on="day", how="left")
    if not sales.empty:
        group = sales.groupby("day", as_index=False).agg(
            sales=("net_qty", "sum"), revenue=("net_revenue", "sum")
        )
        daily = daily.merge(group, on="day", how="left")
    if not ads_kpi.empty:
        group = ads_kpi.groupby("day", as_index=False).agg(
            ad_spend=("spend", "sum"), ad_revenue=("ad_revenue", "sum")
        )
        daily = daily.merge(group, on="day", how="left")
    for col in ["orders", "order_amount", "sales", "revenue", "ad_spend", "ad_revenue"]:
        if col not in daily.columns:
            daily[col] = 0.0
    daily = daily.fillna(0)
    daily["drr"] = np.where(daily["revenue"] > 0, daily["ad_spend"] / daily["revenue"] * 100, 0)

    product_keys = set()
    for frame in (catalog, orders, sales, ads_products, latest_stock):
        if not frame.empty and "nm_id" in frame.columns:
            values = pd.to_numeric(frame["nm_id"], errors="coerce").dropna().astype(int)
            product_keys.update(v for v in values.tolist() if v > 0)
    product_rows = []
    recent_start = max(start, end - timedelta(days=13))
    for nm_id in sorted(product_keys):
        o = valid_orders[valid_orders["nm_id"] == nm_id] if not valid_orders.empty else valid_orders
        s = sales[sales["nm_id"] == nm_id] if not sales.empty else sales
        a = ads_products[ads_products["nm_id"] == nm_id] if not ads_products.empty else ads_products
        st = latest_stock[latest_stock["nm_id"] == nm_id] if not latest_stock.empty else latest_stock
        recent_s = s[s["day"] >= recent_start] if not s.empty else s
        period_days = max(1, (end - recent_start).days + 1)
        avg_daily = float(recent_s["net_qty"].sum()) / period_days if not recent_s.empty else 0.0
        qty = float(pd.to_numeric(st.get("quantity", 0), errors="coerce").fillna(0).sum()) if not st.empty else 0.0
        days_left = qty / avg_daily if avg_daily > 0 else np.nan
        supplier_article = ""
        brand = ""
        product_name = ""
        catalog_row = catalog[catalog["nm_id"] == nm_id] if not catalog.empty else catalog
        if not catalog_row.empty:
            supplier_article = str(catalog_row.iloc[0].get("supplier_article", ""))
            brand = str(catalog_row.iloc[0].get("brand", ""))
            product_name = str(catalog_row.iloc[0].get("product_name", ""))
        else:
            for frame in (o, s):
                if not frame.empty:
                    supplier_article = str(frame.iloc[0].get("supplier_article", ""))
                    brand = str(frame.iloc[0].get("brand", ""))
                    product_name = str(frame.iloc[0].get("subject", ""))
                    break
        cost_row = costs[costs["nm_id"] == nm_id] if not costs.empty else costs
        unit_cost = float(cost_row.iloc[0]["cost_per_wb_unit"]) if not cost_row.empty else 0.0
        net_qty = float(s["net_qty"].sum()) if not s.empty else 0.0
        product_revenue = float(s["net_revenue"].sum()) if not s.empty else 0.0
        product_spend = float(pd.to_numeric(a.get("spend", 0), errors="coerce").fillna(0).sum()) if not a.empty else 0.0
        gross_estimate = product_revenue - product_spend - max(net_qty, 0) * unit_cost
        product_rows.append(
            {
                "Артикул WB": nm_id,
                "Артикул продавца": supplier_article,
                "Товар": product_name,
                "Бренд": brand,
                "Заказы": len(o),
                "Продажи": net_qty,
                "Выручка": product_revenue,
                "Выкуп, %": net_qty / len(o) * 100 if len(o) else 0,
                "Реклама": product_spend,
                "ДРР, %": product_spend / product_revenue * 100 if product_revenue else 0,
                "Остаток": qty,
                "Продаж/день": avg_daily,
                "Запас, дней": days_left,
                "Себестоимость ед.": unit_cost,
                "Маржа до комиссий WB": gross_estimate,
            }
        )
    products = pd.DataFrame(product_rows)
    if not products.empty:
        products = products.sort_values("Выручка", ascending=False)

    ads_summary = pd.DataFrame()
    if not ads_kpi.empty:
        ads_summary = ads_kpi.groupby("campaign_id", as_index=False).agg(
            Расход=("spend", "sum"),
            Показы=("views", "sum"),
            Клики=("clicks", "sum"),
            Заказы=("orders_count", "sum"),
            Продажи_реклама=("ad_revenue", "sum"),
        )
        ads_summary["CTR, %"] = np.where(ads_summary["Показы"] > 0, ads_summary["Клики"] / ads_summary["Показы"] * 100, 0)
        ads_summary["CPC"] = np.where(ads_summary["Клики"] > 0, ads_summary["Расход"] / ads_summary["Клики"], 0)
        ads_summary["ДРР, %"] = np.where(ads_summary["Продажи_реклама"] > 0, ads_summary["Расход"] / ads_summary["Продажи_реклама"] * 100, 0)
        ads_summary = ads_summary.rename(columns={"campaign_id": "Кампания", "Продажи_реклама": "Рекламная выручка"}).sort_values("Расход", ascending=False)

    stock_summary = pd.DataFrame()
    if not latest_stock.empty:
        stock_summary = latest_stock.groupby(["nm_id", "warehouse_name", "region_name"], as_index=False).agg(
            Остаток=("quantity", "sum"),
            К_клиенту=("in_way_to_client", "sum"),
            От_клиента=("in_way_from_client", "sum"),
        )
        stock_summary = stock_summary.rename(
            columns={"nm_id": "Артикул WB", "warehouse_name": "Склад", "region_name": "Регион"}
        ).sort_values(["Артикул WB", "Остаток"], ascending=[True, False])

    alert_rows = []
    if not products.empty:
        for _, row in products.iterrows():
            days_left = row["Запас, дней"]
            if pd.notna(days_left) and days_left < 14:
                alert_rows.append({"Приоритет": "Высокий" if days_left < 7 else "Средний", "Артикул WB": int(row["Артикул WB"]), "Сигнал": f"Запаса примерно на {days_left:.1f} дня"})
            if row["ДРР, %"] > 20 and row["Реклама"] > 0:
                alert_rows.append({"Приоритет": "Средний", "Артикул WB": int(row["Артикул WB"]), "Сигнал": f"Высокий ДРР: {row['ДРР, %']:.1f}%"})
            if row["Выкуп, %"] < 75 and row["Заказы"] >= 10:
                alert_rows.append({"Приоритет": "Средний", "Артикул WB": int(row["Артикул WB"]), "Сигнал": f"Низкий выкуп: {row['Выкуп, %']:.1f}%"})
    alerts = pd.DataFrame(alert_rows)

    financial = {
        # Operational WB statistics (/supplier/sales). These KPIs are used
        # only for portal-like buyout cards; accounting remains based on the
        # realization report below.
        "operational_buyout_amount": revenue,
        "operational_sale_units": sales_count,
        "operational_return_units": returns_count,
        "operational_net_units": net_sales_count,
        "operational_last_change": operational_last_change,
        "gross_sales": 0.0,
        "sales": 0.0,
        "sales_to_pay_gap": 0.0,
        "acquiring": 0.0,
        "commission": 0.0,
        "reward": 0.0,
        "logistics": 0.0,
        "rebill_logistics": 0.0,
        "storage": 0.0,
        "penalties": 0.0,
        "deductions": 0.0,
        "acceptance": 0.0,
        "additional_payment": 0.0,
        "to_pay": 0.0,
        "wb_expenses": 0.0,
        "after_wb": 0.0,
        "cost": 0.0,
        "profit": 0.0,
        "sale_units": 0.0,
        "return_units": 0.0,
        "net_units": 0.0,
        "cost_coverage": 0.0,
        "report_rows": 0.0,
        "unallocated_distributable": 0.0,
        "unallocated_ad_spend": 0.0,
        "common_expense_pool": 0.0,
        "unallocated_deductions": 0.0,
        "unallocated_additional_payment": 0.0,
        "technical_impact": 0.0,
        "product_direct_profit": 0.0,
        "product_allocated_profit": 0.0,
        "remaining_store_impact": 0.0,
        "report_last_date": "",
        # Accounting cards must never silently mix a realization report that
        # ends earlier than the selected period with ads from later days.
        # These fields expose the actual accounting horizon to the UI.
        "financial_calculation_end": "",
        "financial_period_complete": False,
        "financial_lag_days": 0.0,
        "financial_ad_spend": 0.0,
        "allocation_basis": "выручке",
    }
    financial_products = pd.DataFrame()
    financial_unallocated = pd.DataFrame()
    if not financial_df.empty:
        f = financial_df.copy()
        f["operation_date"] = pd.to_datetime(f["operation_date"], errors="coerce")
        f = f.dropna(subset=["operation_date"])
        financial_cutoff = None
        if not f.empty:
            latest_report_date = f["operation_date"].max()
            if pd.notna(latest_report_date):
                latest_report_day = latest_report_date.date()
                financial["report_last_date"] = latest_report_date.strftime("%d.%m.%Y")
                financial_cutoff = min(end, latest_report_day)
                financial["financial_calculation_end"] = financial_cutoff.strftime("%d.%m.%Y")
                financial["financial_period_complete"] = bool(latest_report_day >= end)
                financial["financial_lag_days"] = float(max((end - latest_report_day).days, 0))
        f = f[(f["operation_date"].dt.date >= start) & (f["operation_date"].dt.date <= end)]

        # The financial result is accounting-based, so advertising expenses used
        # inside that result must cover the same date horizon as the realization
        # report. Otherwise a report ending on (say) the 16th would be reduced by
        # advertising spend from the 17th, understating profit.
        finance_ads_kpi = ads_kpi
        finance_ads_products = ads_products
        if financial_cutoff is not None:
            if financial_cutoff < start:
                finance_ads_kpi = ads_kpi.iloc[0:0].copy() if not ads_kpi.empty else ads_kpi
                finance_ads_products = ads_products.iloc[0:0].copy() if not ads_products.empty else ads_products
            else:
                if not ads_kpi.empty and "day" in ads_kpi.columns:
                    finance_ads_kpi = ads_kpi[ads_kpi["day"] <= financial_cutoff].copy()
                if not ads_products.empty and "day" in ads_products.columns:
                    finance_ads_products = ads_products[ads_products["day"] <= financial_cutoff].copy()
        else:
            finance_ads_kpi = ads_kpi.iloc[0:0].copy() if not ads_kpi.empty else ads_kpi
            finance_ads_products = ads_products.iloc[0:0].copy() if not ads_products.empty else ads_products
        finance_ad_spend = float(pd.to_numeric(finance_ads_kpi.get("spend", 0), errors="coerce").fillna(0).sum()) if not finance_ads_kpi.empty else 0.0
        financial["financial_ad_spend"] = finance_ad_spend

        numeric_columns = [
            "retail_amount", "retail_price_withdisc_rub", "ppvz_for_pay",
            "ppvz_sales_commission", "commission", "ppvz_reward",
            "delivery_rub", "rebill_logistic_cost", "storage_fee",
            "penalty", "deduction", "acceptance", "additional_payment",
            "quantity", "nm_id", "acquiring_fee",
        ]
        for col in numeric_columns:
            if col not in f.columns:
                f[col] = 0.0
            f[col] = pd.to_numeric(f[col], errors="coerce").fillna(0)
        for col in ["doc_type_name", "operation_name"]:
            if col not in f.columns:
                f[col] = ""
            f[col] = f[col].fillna("").astype(str)

        operation_text = (f["doc_type_name"] + " " + f["operation_name"]).str.lower()
        is_return = operation_text.str.contains("возврат", regex=False)
        is_sale = operation_text.str.contains("продаж", regex=False) & ~is_return
        quantity_abs = f["quantity"].abs()

        # Quantity is meaningful only on sale/return rows. Logistics, storage,
        # penalties and other service rows can also contain quantity-like values;
        # counting them caused heavily inflated unit totals in v2.1.
        f["signed_units"] = np.where(
            is_return,
            -quantity_abs,
            np.where(is_sale, quantity_abs, 0),
        )

        # retail_amount is the report field "Сумма продаж (возвратов)" and
        # already carries the operation sign. It is safer than summing a unit price.
        financial["gross_sales"] = float((f["retail_price_withdisc_rub"] * f["signed_units"]).sum())
        financial["sales"] = float(f["retail_amount"].sum())
        if abs(financial["sales"]) < 0.01:
            financial["sales"] = financial["gross_sales"]

        financial["to_pay"] = float(f["ppvz_for_pay"].sum())
        financial["sales_to_pay_gap"] = financial["gross_sales"] - financial["to_pay"]
        financial["acquiring"] = float(f["acquiring_fee"].sum())
        commission_source = f["ppvz_sales_commission"]
        if commission_source.abs().sum() < 0.01:
            commission_source = f["commission"]
        financial["commission"] = float(commission_source.sum())
        financial["reward"] = float(f["ppvz_reward"].sum())
        financial["logistics"] = float(f["delivery_rub"].sum())
        financial["rebill_logistics"] = float(f["rebill_logistic_cost"].sum())
        financial["storage"] = float(f["storage_fee"].sum())
        financial["penalties"] = float(f["penalty"].sum())
        financial["deductions"] = float(f["deduction"].sum())
        financial["acceptance"] = float(f["acceptance"].sum())
        financial["additional_payment"] = float(f["additional_payment"].sum())
        financial["sale_units"] = float(quantity_abs[is_sale].sum())
        financial["return_units"] = float(quantity_abs[is_return].sum())
        financial["net_units"] = financial["sale_units"] - financial["return_units"]
        financial["report_rows"] = float(len(f))

        if not costs.empty:
            cost_map = costs.set_index("nm_id")["cost_per_wb_unit"].to_dict()
            units_by_nm = f.groupby("nm_id", as_index=False)["signed_units"].sum()
            cost_total = 0.0
            units_with_cost = 0.0
            positive_units = 0.0
            for _, row in units_by_nm.iterrows():
                nm_id = int(row["nm_id"] or 0)
                units = max(float(row["signed_units"]), 0.0)
                if units <= 0:
                    continue
                positive_units += units
                unit_cost = float(cost_map.get(nm_id, 0) or 0)
                if unit_cost > 0:
                    units_with_cost += units
                    cost_total += units * unit_cost
            financial["cost"] = cost_total
            financial["cost_coverage"] = units_with_cost / positive_units * 100 if positive_units else 100.0

        # ppvz_for_pay is already "К перечислению продавцу за реализованный товар",
        # therefore WB commission must not be subtracted a second time.
        financial["wb_expenses"] = (
            financial["logistics"]
            + financial["rebill_logistics"]
            + financial["storage"]
            + financial["penalties"]
            + financial["deductions"]
            + financial["acceptance"]
        )
        financial["after_wb"] = (
            financial["to_pay"]
            + financial["additional_payment"]
            - financial["wb_expenses"]
        )
        financial["profit"] = financial["after_wb"] - finance_ad_spend - financial["cost"]

        # v2.6: article economics has two layers:
        # 1) direct contribution, containing only operations tied to the article;
        # 2) calculated article profit after allocating common store expenses.
        # Deductions and technical/inactive article rows remain at store level and
        # are deliberately not spread across products.
        fp = f.copy()
        if "supplier_article" not in fp.columns:
            fp["supplier_article"] = ""
        fp["sale_units"] = np.where(is_sale, quantity_abs, 0)
        fp["return_units"] = np.where(is_return, quantity_abs, 0)
        fp["buyer_revenue"] = fp["retail_price_withdisc_rub"] * fp["signed_units"]

        grouped = fp.groupby(["nm_id"], as_index=False).agg(
            supplier_article=("supplier_article", lambda x: next((str(v) for v in x if str(v)), "")),
            sale_units=("sale_units", "sum"),
            return_units=("return_units", "sum"),
            units=("signed_units", "sum"),
            buyer_revenue=("buyer_revenue", "sum"),
            sales=("retail_amount", "sum"),
            to_pay=("ppvz_for_pay", "sum"),
            logistics=("delivery_rub", "sum"),
            rebill_logistics=("rebill_logistic_cost", "sum"),
            storage=("storage_fee", "sum"),
            penalties=("penalty", "sum"),
            deductions=("deduction", "sum"),
            acceptance=("acceptance", "sum"),
            additional_payment=("additional_payment", "sum"),
        )
        ad_by_nm = pd.DataFrame(columns=["nm_id", "ad_spend"])
        if not finance_ads_products.empty and "nm_id" in finance_ads_products.columns:
            ad_by_nm = finance_ads_products.groupby("nm_id", as_index=False)["spend"].sum().rename(columns={"spend": "ad_spend"})
        grouped = grouped.merge(ad_by_nm, on="nm_id", how="outer")
        grouped["nm_id"] = pd.to_numeric(grouped["nm_id"], errors="coerce").fillna(0).astype(int)
        grouped_numeric = [
            "sale_units", "return_units", "units", "buyer_revenue", "sales", "to_pay",
            "logistics", "rebill_logistics", "storage", "penalties", "deductions",
            "acceptance", "additional_payment", "ad_spend",
        ]
        for col in grouped_numeric:
            if col not in grouped.columns:
                grouped[col] = 0.0
            grouped[col] = pd.to_numeric(grouped[col], errors="coerce").fillna(0.0)
        # Product allocation from the promotion API can occasionally sum above
        # the authoritative campaign total because of application-level rows.
        # In that case preserve product proportions but normalize to the KPI total.
        raw_product_ad_total = float(pd.to_numeric(grouped["ad_spend"], errors="coerce").fillna(0).sum())
        if raw_product_ad_total > 0 and float(finance_ad_spend) >= 0 and raw_product_ad_total > float(finance_ad_spend) + 0.01:
            grouped["ad_spend"] = grouped["ad_spend"] * (float(finance_ad_spend) / raw_product_ad_total)

        # Enrich with the current active catalog. Inactive, unknown and technical
        # rows are preserved for store-level reconciliation but excluded from ranking.
        active_catalog_ids: set[int] = set()
        if not catalog.empty and "nm_id" in catalog.columns:
            active_catalog_ids = set(pd.to_numeric(catalog["nm_id"], errors="coerce").dropna().astype(int).tolist())
            catalog_cols = [c for c in ["nm_id", "supplier_article", "product_name", "subject_name", "brand"] if c in catalog.columns]
            catalog_fin = catalog[catalog_cols].drop_duplicates("nm_id").copy()
            catalog_fin = catalog_fin.rename(columns={
                "supplier_article": "catalog_article",
                "product_name": "product_name",
                "subject_name": "subject_name",
                "brand": "brand",
            })
            grouped = grouped.merge(catalog_fin, on="nm_id", how="left")
            grouped["supplier_article"] = grouped["catalog_article"].fillna("").where(
                grouped["catalog_article"].fillna("").astype(str).str.len() > 0,
                grouped["supplier_article"],
            )
            grouped = grouped.drop(columns=["catalog_article"], errors="ignore")
        for col in ["product_name", "subject_name", "brand"]:
            if col not in grouped.columns:
                grouped[col] = ""
            grouped[col] = grouped[col].fillna("").astype(str)
        grouped["supplier_article"] = grouped["supplier_article"].fillna("").astype(str)

        cost_map = costs.set_index("nm_id")["cost_per_wb_unit"].to_dict() if not costs.empty else {}
        grouped["unit_cost"] = grouped["nm_id"].map(cost_map).fillna(0)
        grouped["cost"] = grouped["units"].clip(lower=0) * grouped["unit_cost"]
        grouped["wb_expenses"] = grouped[["logistics", "rebill_logistics", "storage", "penalties", "deductions", "acceptance"]].sum(axis=1)
        grouped["after_wb"] = grouped["to_pay"] + grouped["additional_payment"] - grouped["wb_expenses"]
        grouped["direct_profit"] = grouped["after_wb"] - grouped["ad_spend"] - grouped["cost"]

        name_text = grouped["supplier_article"].astype(str) + " " + grouped["product_name"].astype(str)
        unknown_name = _contains_any_text(name_text, ("неопознан", "unknown", "техническ", "служебн"))
        if active_catalog_ids:
            active_mask = grouped["nm_id"].isin(active_catalog_ids) & ~unknown_name
        else:
            active_mask = (grouped["nm_id"] > 0) & ~unknown_name
        unallocated_mask = grouped["nm_id"] == 0
        technical_mask = (grouped["nm_id"] > 0) & ~active_mask

        active = grouped[active_mask].copy()
        unallocated = grouped[unallocated_mask].copy()
        technical = grouped[technical_mask].copy()

        # Only genuinely common expenses are allocated. Deductions remain a
        # separate store-level line because their economic nature may not be product-specific.
        distributable_columns = ["logistics", "rebill_logistics", "storage", "penalties", "acceptance"]
        unallocated_distributable = float(unallocated[distributable_columns].sum(axis=1).sum()) if not unallocated.empty else 0.0
        direct_ad_total = float(grouped["ad_spend"].sum()) if not grouped.empty else 0.0
        unallocated_ad = max(0.0, float(finance_ad_spend) - direct_ad_total)
        common_pool = unallocated_distributable + unallocated_ad
        financial["unallocated_distributable"] = unallocated_distributable
        financial["unallocated_ad_spend"] = unallocated_ad
        financial["common_expense_pool"] = common_pool
        financial["unallocated_deductions"] = float(unallocated["deductions"].sum()) if not unallocated.empty else 0.0
        financial["unallocated_additional_payment"] = float(unallocated["additional_payment"].sum()) if not unallocated.empty else 0.0
        financial["technical_impact"] = float(technical["direct_profit"].sum()) if not technical.empty else 0.0

        if not active.empty:
            revenue_base = active["buyer_revenue"].abs()
            if revenue_base.sum() > 0:
                active["allocation_share"] = revenue_base / revenue_base.sum()
                allocation_basis = "выручке"
            else:
                unit_base = active["units"].clip(lower=0)
                active["allocation_share"] = unit_base / unit_base.sum() if unit_base.sum() > 0 else 0.0
                allocation_basis = "количеству"
            active["allocated_common_expense"] = active["allocation_share"] * common_pool
            active["allocated_profit"] = active["direct_profit"] - active["allocated_common_expense"]
            active["direct_margin_pct"] = np.where(active["buyer_revenue"].abs() > 0, active["direct_profit"] / active["buyer_revenue"].abs() * 100, 0)
            active["allocated_margin_pct"] = np.where(active["buyer_revenue"].abs() > 0, active["allocated_profit"] / active["buyer_revenue"].abs() * 100, 0)
            active["direct_profit_per_unit"] = np.where(active["units"] > 0, active["direct_profit"] / active["units"], 0)
            active["allocated_profit_per_unit"] = np.where(active["units"] > 0, active["allocated_profit"] / active["units"], 0)
            active["return_rate_pct"] = np.where(active["sale_units"] > 0, active["return_units"] / active["sale_units"] * 100, 0)
            active["ad_share_pct"] = np.where(active["buyer_revenue"].abs() > 0, active["ad_spend"] / active["buyer_revenue"].abs() * 100, 0)
            active["wb_share_pct"] = np.where(active["buyer_revenue"].abs() > 0, active["wb_expenses"] / active["buyer_revenue"].abs() * 100, 0)
            active["cost_share_pct"] = np.where(active["buyer_revenue"].abs() > 0, active["cost"] / active["buyer_revenue"].abs() * 100, 0)
            active["total_variable_cost"] = active["wb_expenses"] + active["ad_spend"] + active["cost"] + active["allocated_common_expense"]
            active["roi_pct"] = np.where(active["total_variable_cost"] > 0, active["allocated_profit"] / active["total_variable_cost"] * 100, 0)
            eligible = active["sale_units"] > 1
            active["rank"] = 0
            if eligible.any():
                active.loc[eligible, "rank"] = active.loc[eligible, "allocated_profit"].rank(method="min", ascending=False).astype(int)
                profit_q75 = float(active.loc[eligible, "allocated_profit"].quantile(0.75)) if eligible.sum() > 1 else float(active.loc[eligible, "allocated_profit"].max())
            else:
                profit_q75 = 0.0
            active["status"] = np.select(
                [
                    ~eligible,
                    active["allocated_profit"] < 0,
                    active["allocated_margin_pct"] < 10,
                    active["allocated_profit"] >= profit_q75,
                ],
                ["Недостаточно данных", "Убыточный", "Низкая маржа", "Лидер"],
                default="Прибыльный",
            )
            positive_profit = active["allocated_profit"].clip(lower=0)
            active["profit_share_pct"] = positive_profit / positive_profit.sum() * 100 if positive_profit.sum() > 0 else 0.0

            # Add current stock and recent sales velocity to financial economics.
            product_stock = pd.DataFrame(columns=["nm_id", "stock_qty", "avg_daily_sales", "days_left"])
            if not products.empty:
                product_stock = products[["Артикул WB", "Остаток", "Продаж/день", "Запас, дней"]].copy()
                product_stock = product_stock.rename(columns={
                    "Артикул WB": "nm_id",
                    "Остаток": "stock_qty",
                    "Продаж/день": "avg_daily_sales",
                    "Запас, дней": "days_left",
                })
            active = active.merge(product_stock, on="nm_id", how="left")
            for col in ["stock_qty", "avg_daily_sales", "days_left"]:
                if col not in active.columns:
                    active[col] = np.nan if col == "days_left" else 0.0
            active["stock_qty"] = pd.to_numeric(active["stock_qty"], errors="coerce").fillna(0.0)
            active["avg_daily_sales"] = pd.to_numeric(active["avg_daily_sales"], errors="coerce").fillna(0.0)
            active["days_left"] = pd.to_numeric(active["days_left"], errors="coerce")

            active["risk_reason"] = active.apply(_risk_reason, axis=1)
            active["recommended_action"] = active.apply(_recommended_action, axis=1)
            active["reason_action"] = active["risk_reason"] + ". " + active["recommended_action"]
            financial["allocation_basis"] = allocation_basis
            financial["product_direct_profit"] = float(active["direct_profit"].sum())
            financial["product_allocated_profit"] = float(active["allocated_profit"].sum())
        else:
            for col in ["allocation_share", "allocated_common_expense", "allocated_profit", "direct_margin_pct", "allocated_margin_pct", "direct_profit_per_unit", "allocated_profit_per_unit", "return_rate_pct", "ad_share_pct", "wb_share_pct", "cost_share_pct", "total_variable_cost", "roi_pct", "profit_share_pct"]:
                active[col] = 0.0
            active["rank"] = 0
            active["status"] = ""
            active["stock_qty"] = 0.0
            active["avg_daily_sales"] = 0.0
            active["days_left"] = np.nan
            active["risk_reason"] = ""
            active["recommended_action"] = ""
            active["reason_action"] = ""
            financial["allocation_basis"] = "выручке"
            financial["product_direct_profit"] = 0.0
            financial["product_allocated_profit"] = 0.0

        # Store-level rows: common operations, deductions and cards outside the active catalog.
        store_rows = []
        if not unallocated.empty:
            ua = unallocated.iloc[0]
            store_rows.append({
                "Тип": "Общие операции без артикула",
                "Артикул WB": 0,
                "Артикул продавца": "",
                "Товар": "Операции без привязки к товару",
                "Логистика": float(ua.get("logistics", 0)),
                "Доп. логистика": float(ua.get("rebill_logistics", 0)),
                "Хранение": float(ua.get("storage", 0)),
                "Штрафы": float(ua.get("penalties", 0)),
                "Удержания": float(ua.get("deductions", 0)),
                "Приёмка": float(ua.get("acceptance", 0)),
                "Доплаты WB": float(ua.get("additional_payment", 0)),
                "Реклама": unallocated_ad,
                "Влияние на прибыль": float(ua.get("direct_profit", 0)) - unallocated_ad,
            })
        if not technical.empty:
            for _, tr in technical.iterrows():
                store_rows.append({
                    "Тип": "Вне активного каталога / техническая строка",
                    "Артикул WB": int(tr.get("nm_id", 0) or 0),
                    "Артикул продавца": str(tr.get("supplier_article", "") or ""),
                    "Товар": str(tr.get("product_name", "") or "Неопознанный товар"),
                    "Логистика": float(tr.get("logistics", 0)),
                    "Доп. логистика": float(tr.get("rebill_logistics", 0)),
                    "Хранение": float(tr.get("storage", 0)),
                    "Штрафы": float(tr.get("penalties", 0)),
                    "Удержания": float(tr.get("deductions", 0)),
                    "Приёмка": float(tr.get("acceptance", 0)),
                    "Доплаты WB": float(tr.get("additional_payment", 0)),
                    "Реклама": float(tr.get("ad_spend", 0)),
                    "Влияние на прибыль": float(tr.get("direct_profit", 0)),
                })
        financial_unallocated = pd.DataFrame(store_rows)
        financial["remaining_store_impact"] = financial["profit"] - financial.get("product_allocated_profit", 0.0)

        financial_products = active.rename(columns={
            "rank": "Ранг",
            "status": "Статус",
            "nm_id": "Артикул WB",
            "supplier_article": "Артикул продавца",
            "product_name": "Товар",
            "subject_name": "Категория",
            "sale_units": "Продажи, шт",
            "return_units": "Возвраты, шт",
            "units": "Продано нетто",
            "return_rate_pct": "Возвраты, %",
            "buyer_revenue": "Выкупы по цене покупателя",
            "sales": "Продажи по отчёту",
            "to_pay": "К перечислению за товар",
            "wb_expenses": "Прямые расходы WB",
            "ad_spend": "Реклама",
            "ad_share_pct": "Доля рекламы, %",
            "wb_share_pct": "Доля расходов WB, %",
            "unit_cost": "Себестоимость ед.",
            "cost": "Себестоимость",
            "cost_share_pct": "Доля себестоимости, %",
            "direct_profit": "Маржинальная прибыль",
            "direct_profit_per_unit": "Маржинальная прибыль на ед.",
            "direct_margin_pct": "Маржинальность, %",
            "allocation_share": "Доля общих расходов, %",
            "allocated_common_expense": "Распределено общих расходов",
            "allocated_profit": "Расчётная прибыль",
            "allocated_profit_per_unit": "Расчётная прибыль на ед.",
            "allocated_margin_pct": "Расчётная маржа, %",
            "roi_pct": "Рентабельность затрат, %",
            "profit_share_pct": "Доля прибыли, %",
            "stock_qty": "Остаток",
            "avg_daily_sales": "Продаж/день",
            "days_left": "Запас, дней",
            "risk_reason": "Основная причина",
            "recommended_action": "Рекомендация",
            "reason_action": "Причина / действие",
        })[[
            "Ранг", "Статус", "Артикул WB", "Артикул продавца", "Товар", "Категория",
            "Продажи, шт", "Возвраты, шт", "Продано нетто", "Возвраты, %",
            "Выкупы по цене покупателя", "Продажи по отчёту", "К перечислению за товар",
            "Прямые расходы WB", "Реклама", "Доля рекламы, %", "Доля расходов WB, %",
            "Себестоимость ед.", "Себестоимость", "Доля себестоимости, %",
            "Маржинальная прибыль", "Маржинальная прибыль на ед.", "Маржинальность, %",
            "Доля общих расходов, %", "Распределено общих расходов",
            "Расчётная прибыль", "Расчётная прибыль на ед.", "Расчётная маржа, %",
            "Рентабельность затрат, %", "Доля прибыли, %",
            "Остаток", "Продаж/день", "Запас, дней",
            "Основная причина", "Рекомендация", "Причина / действие"
        ]]
        if not financial_products.empty:
            financial_products["Доля общих расходов, %"] = financial_products["Доля общих расходов, %"] * 100
            financial_products["_insufficient"] = (financial_products["Статус"] == "Недостаточно данных").astype(int)
            financial_products = financial_products.sort_values(
                ["_insufficient", "Расчётная прибыль"], ascending=[True, False]
            ).drop(columns=["_insufficient"])

    hourly = pd.DataFrame({"hour": list(range(24)), "order_amount": 0.0, "revenue": 0.0})
    if start == end:
        if not valid_orders.empty:
            tmp = valid_orders.copy(); tmp["hour"] = tmp["order_date"].dt.hour
            g = tmp.groupby("hour", as_index=False)["order_amount"].sum()
            hourly = hourly.drop(columns=["order_amount"]).merge(g, on="hour", how="left")
        if not sales.empty:
            tmp = sales.copy(); tmp["hour"] = tmp["sale_date"].dt.hour
            g = tmp.groupby("hour", as_index=False)["net_revenue"].sum().rename(columns={"net_revenue":"revenue"})
            hourly = hourly.drop(columns=["revenue"]).merge(g, on="hour", how="left")
        hourly = hourly.fillna(0)

    return DashboardData(kpi=kpi, daily=daily, products=products, ads=ads_summary, stocks=stock_summary, alerts=alerts, financial=financial, financial_products=financial_products, financial_unallocated=financial_unallocated, hourly=hourly)
