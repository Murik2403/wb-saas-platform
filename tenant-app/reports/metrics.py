"""Preset report metrics: (start, end) -> (DataFrame, matplotlib.Figure).

pdf_builder.py doesn't know anything about a specific metric's data shape --
it just asks each requested metric code to draw itself and reports the
underlying numbers in a short summary line.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import matplotlib
matplotlib.use("Agg")  # headless -- no display available in a container
import matplotlib.pyplot as plt
import pandas as pd

from calculations import build_dashboard
from db import read_table

METRIC_LABELS = {
    "sales_orders": "Заказы и продажи по дням",
    "ads": "Рекламные расходы и ДРР по дням",
    "stocks": "Остатки: топ-20 товаров",
    "pnl": "Финансовый результат (P&L)",
    "abc_analysis": "ABC-анализ товаров по прибыли",
    "buyout_rate": "Процент выкупа по товарам",
    "oos_risk": "Риск Out-of-Stock (дни запаса)",
    "wb_deductions": "Структура удержаний WB",
    "ad_performance": "Эффективность рекламы по кампаниям",
    "warehouse_stocks": "География остатков по складам",
}


@dataclass
class MetricResult:
    title: str
    figure: "plt.Figure"
    summary: str


def _sales_orders(start: date, end: date) -> MetricResult:
    daily = build_dashboard(start, end).daily
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.plot(daily["day"], daily["orders"], label="Заказы, шт.", marker="o")
    ax.plot(daily["day"], daily["sales"], label="Продажи, шт.", marker="o")
    ax.set_xlabel("Дата")
    ax.set_ylabel("Штук")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    total_orders = int(daily["orders"].sum())
    total_sales = int(daily["sales"].sum())
    return MetricResult(
        title=METRIC_LABELS["sales_orders"], figure=fig,
        summary=f"Итого за период: заказов {total_orders}, продаж {total_sales}.",
    )


def _ads(start: date, end: date) -> MetricResult:
    daily = build_dashboard(start, end).daily
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.bar(daily["day"], daily["ad_spend"], color="#7c6cf6", label="Расход, ₽")
    ax2 = ax.twinx()
    ax2.plot(daily["day"], daily["drr"], color="#f2677a", label="ДРР, %", marker="o")
    ax.set_xlabel("Дата")
    ax.set_ylabel("Расход, ₽")
    ax2.set_ylabel("ДРР, %")
    fig.legend(loc="upper left", bbox_to_anchor=(0.08, 0.92))
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    total_spend = float(daily["ad_spend"].sum())
    avg_drr = float(daily["drr"].mean()) if len(daily) else 0.0
    return MetricResult(
        title=METRIC_LABELS["ads"], figure=fig,
        summary=f"Итого расход за период: {total_spend:.0f}₽, средний ДРР {avg_drr:.1f}%.",
    )


def _stocks(start: date, end: date) -> MetricResult:
    stocks = read_table("stocks")
    fig, ax = plt.subplots(figsize=(7, 4))
    if stocks.empty:
        ax.text(0.5, 0.5, "Нет данных об остатках", ha="center", va="center")
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["stocks"], figure=fig, summary="Данных об остатках нет.")

    # `stocks` accumulates one row per sync snapshot, not just the latest --
    # grouping by nm_id without filtering to the newest snapshot_at first
    # sums quantities across every past snapshot, wildly inflating totals.
    latest_snapshot = stocks["snapshot_at"].max()
    stocks = stocks[stocks["snapshot_at"] == latest_snapshot]

    catalog = read_table("products_catalog")
    grouped = stocks.groupby("nm_id", as_index=False)["quantity"].sum()
    grouped = grouped.sort_values("quantity", ascending=False).head(20)
    if not catalog.empty and "nm_id" in catalog.columns:
        grouped = grouped.merge(catalog[["nm_id", "supplier_article"]], on="nm_id", how="left")
    grouped["label"] = grouped.get("supplier_article").fillna(grouped["nm_id"].astype(str)) if "supplier_article" in grouped.columns else grouped["nm_id"].astype(str)

    ax.barh(grouped["label"].astype(str), grouped["quantity"], color="#3ecf8e")
    ax.set_xlabel("Остаток, шт.")
    ax.invert_yaxis()
    fig.tight_layout()
    total_qty = int(grouped["quantity"].sum())
    return MetricResult(
        title=METRIC_LABELS["stocks"], figure=fig,
        summary=f"Показаны топ-{len(grouped)} товаров, суммарный остаток по ним {total_qty} шт.",
    )


def _pnl(start: date, end: date) -> MetricResult:
    # Reuses build_dashboard()'s already-computed store-level P&L (fallback
    # commission fields, WB expense definitions, etc.) rather than re-deriving
    # it from raw tables here -- see calculations.py's financial dict, which
    # is the same source the Финансы page shows the user.
    data = build_dashboard(start, end)
    fin = data.financial
    fig, ax = plt.subplots(figsize=(7, 3.5))

    sales = float(fin.get("sales", 0.0))
    wb_exp = float(fin.get("wb_expenses", 0.0))
    cost = float(fin.get("cost", 0.0))
    ads = float(fin.get("financial_ad_spend", 0.0))
    profit = float(fin.get("profit", 0.0))

    categories = ["Продажи", "Расходы WB", "Себестоимость", "Реклама", "Чистая прибыль"]
    values = [sales, wb_exp, cost, ads, profit]
    colors = ["#2e86de", "#ee5253", "#ff9f43", "#9c88ff", "#10ac84" if profit >= 0 else "#ee5253"]

    bars = ax.barh(categories, values, color=colors)
    ax.set_xlabel("Сумма, ₽")
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.3)

    max_abs = max(abs(v) for v in values) if values else 0
    for bar in bars:
        w = bar.get_width()
        ax.text(
            w + (max_abs * 0.01 if w >= 0 else -max_abs * 0.01),
            bar.get_y() + bar.get_height() / 2,
            f"{w:,.0f} ₽".replace(",", " "),
            va="center", ha="left" if w >= 0 else "right", fontsize=8,
        )

    fig.tight_layout()
    summary = (
        f"Выручка: {sales:,.0f}₽, удержания WB: {wb_exp:,.0f}₽, "
        f"себестоимость: {cost:,.0f}₽, реклама: {ads:,.0f}₽. "
        f"Чистая прибыль: {profit:,.0f}₽."
    ).replace(",", " ")
    return MetricResult(title=METRIC_LABELS["pnl"], figure=fig, summary=summary)


def _abc_analysis(start: date, end: date) -> MetricResult:
    data = build_dashboard(start, end)
    df = data.financial_products.copy()
    fig, ax = plt.subplots(figsize=(7, 3.8))

    if df.empty or "Расчётная прибыль" not in df.columns:
        ax.text(0.5, 0.5, "Нет данных для ABC-анализа", ha="center", va="center")
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["abc_analysis"], figure=fig, summary="Нет данных для ABC-анализа.")

    df = df[df["Расчётная прибыль"] > 0].sort_values("Расчётная прибыль", ascending=False)
    if df.empty:
        ax.text(0.5, 0.5, "Прибыльные товары отсутствуют", ha="center", va="center")
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["abc_analysis"], figure=fig, summary="Прибыльные товары за период отсутствуют.")

    total_profit = df["Расчётная прибыль"].sum()
    df["cum_profit_pct"] = (df["Расчётная прибыль"].cumsum() / total_profit) * 100
    df["group"] = df["cum_profit_pct"].apply(lambda p: "A" if p <= 80 else ("B" if p <= 95 else "C"))
    # A single dominant top item can cross the 80% line on its own row --
    # it's still the whole of group A in that case, not group B/C.
    if not df.empty:
        df.iloc[0, df.columns.get_loc("group")] = "A"

    group_counts = df["group"].value_counts().to_dict()
    cnt_a, cnt_b, cnt_c = group_counts.get("A", 0), group_counts.get("B", 0), group_counts.get("C", 0)

    top_df = df.head(15)
    labels = top_df["Артикул продавца"].fillna(top_df["Артикул WB"].astype(str)).astype(str)

    ax.bar(labels, top_df["Расчётная прибыль"], color="#7c6cf6", label="Прибыль, ₽")
    ax.set_ylabel("Прибыль, ₽")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)

    ax2 = ax.twinx()
    ax2.plot(labels, top_df["cum_profit_pct"], color="#f2677a", marker="o", linewidth=2, label="Накопленная доля, %")
    ax2.set_ylabel("Накопленная доля, %")
    ax2.set_ylim(0, 105)
    ax2.axhline(80, color="gray", linestyle="--", alpha=0.5)
    ax2.axhline(95, color="gray", linestyle=":", alpha=0.5)

    fig.tight_layout()
    summary = (
        f"Группа A (80% прибыли): {cnt_a} тов., группа B (15%): {cnt_b} тов., группа C (5%): {cnt_c} тов. "
        f"Всего прибыльных товаров: {len(df)}."
    )
    return MetricResult(title=METRIC_LABELS["abc_analysis"], figure=fig, summary=summary)


def _buyout_rate(start: date, end: date) -> MetricResult:
    data = build_dashboard(start, end)
    df = data.financial_products.copy()
    fig, ax = plt.subplots(figsize=(7, 3.8))

    if df.empty or "Продажи, шт" not in df.columns or "Возвраты, %" not in df.columns:
        ax.text(0.5, 0.5, "Нет данных по выкупу товаров", ha="center", va="center")
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["buyout_rate"], figure=fig, summary="Данных по выкупу товаров нет.")

    df = df[df["Продажи, шт"] > 0].sort_values("Продажи, шт", ascending=False).head(15)
    if df.empty:
        ax.text(0.5, 0.5, "Нет продаж за указанный период", ha="center", va="center")
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["buyout_rate"], figure=fig, summary="Продаж за период не обнаружено.")

    df["buyout_pct"] = 100.0 - df["Возвраты, %"].clip(lower=0.0, upper=100.0)
    labels = df["Артикул продавца"].fillna(df["Артикул WB"].astype(str)).astype(str)

    bars = ax.barh(labels, df["buyout_pct"], color="#3ecf8e")
    ax.set_xlabel("% выкупа")
    ax.set_xlim(0, 105)
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.3)

    for bar in bars:
        w = bar.get_width()
        ax.text(w + 1, bar.get_y() + bar.get_height() / 2, f"{w:.1f}%", va="center", fontsize=8)

    fig.tight_layout()
    avg_buyout = float(df["buyout_pct"].mean())
    total_net = int(df["Продано нетто"].sum()) if "Продано нетто" in df.columns else int(df["Продажи, шт"].sum())
    summary = f"Средний % выкупа по топ-товарам: {avg_buyout:.1f}%. Продано нетто: {total_net} шт."
    return MetricResult(title=METRIC_LABELS["buyout_rate"], figure=fig, summary=summary)


def _oos_risk(start: date, end: date) -> MetricResult:
    data = build_dashboard(start, end)
    df = data.financial_products.copy()
    fig, ax = plt.subplots(figsize=(7, 3.8))

    if df.empty or "Запас, дней" not in df.columns:
        ax.text(0.5, 0.5, "Нет данных о днях запаса", ha="center", va="center")
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["oos_risk"], figure=fig, summary="Данных о днях запаса нет.")

    df = df[df["Остаток"] > 0].sort_values("Запас, дней", ascending=True).head(15)
    if df.empty:
        ax.text(0.5, 0.5, "Нет товаров на остатке", ha="center", va="center")
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["oos_risk"], figure=fig, summary="Товаров с остатками не найдено.")

    labels = df["Артикул продавца"].fillna(df["Артикул WB"].astype(str)).astype(str)
    colors = ["#f2677a" if d < 7 else ("#f39c12" if d <= 14 else "#3ecf8e") for d in df["Запас, дней"]]

    bars = ax.barh(labels, df["Запас, дней"], color=colors)
    ax.axvline(7, color="#f2677a", linestyle="--", linewidth=1.5, label="Порог OOS (7 дн.)")
    ax.set_xlabel("Дни запаса")
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.3)
    ax.legend(loc="lower right")

    for bar in bars:
        w = bar.get_width()
        ax.text(w + 0.5, bar.get_y() + bar.get_height() / 2, f"{w:.0f} дн", va="center", fontsize=8)

    fig.tight_layout()
    critical_cnt = int((df["Запас, дней"] < 7).sum())
    min_days = float(df["Запас, дней"].min()) if not df.empty else 0.0
    summary = f"Товаров с риском OOS (<7 дн.): {critical_cnt} шт. Минимальный запас: {min_days:.0f} дн."
    return MetricResult(title=METRIC_LABELS["oos_risk"], figure=fig, summary=summary)


def _wb_deductions(start: date, end: date) -> MetricResult:
    data = build_dashboard(start, end)
    fin = data.financial
    fig, ax = plt.subplots(figsize=(7, 3.5))

    categories = {
        "Логистика": float(fin.get("logistics", 0.0)) + float(fin.get("rebill_logistics", 0.0)),
        "Хранение": float(fin.get("storage", 0.0)),
        "Штрафы": float(fin.get("penalties", 0.0)),
        "Прочие удержания": float(fin.get("deductions", 0.0)),
        "Платная приёмка": float(fin.get("acceptance", 0.0)),
    }
    items = {k: v for k, v in categories.items() if v > 0}

    if not items:
        ax.text(0.5, 0.5, "Удержания WB отсутствуют", ha="center", va="center")
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["wb_deductions"], figure=fig, summary="Удержаний WB за период нет.")

    labels = list(items.keys())
    values = list(items.values())
    colors = ["#7c6cf6", "#48dbfb", "#ff9f43", "#f2677a", "#10ac84"][: len(values)]

    wedges, texts, autotexts = ax.pie(
        values, labels=labels, autopct="%1.1f%%", startangle=140, colors=colors,
        wedgeprops=dict(width=0.4, edgecolor="w"),
    )
    plt.setp(autotexts, size=8, weight="bold")
    plt.setp(texts, size=8)

    fig.tight_layout()
    total_exp = sum(values)
    top_cat = max(items, key=items.get)
    summary = (
        f"Сумма удержаний WB: {total_exp:,.0f}₽. Основная статья: {top_cat} ({items[top_cat]:,.0f}₽)."
    ).replace(",", " ")
    return MetricResult(title=METRIC_LABELS["wb_deductions"], figure=fig, summary=summary)


def _ad_performance(start: date, end: date) -> MetricResult:
    data = build_dashboard(start, end)
    ads = data.ads.copy()
    fig, ax = plt.subplots(figsize=(7, 3.8))

    if ads.empty or "Расход" not in ads.columns:
        ax.text(0.5, 0.5, "Нет данных по рекламным кампаниям", ha="center", va="center")
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["ad_performance"], figure=fig, summary="Данных по рекламе нет.")

    ads = ads[ads["Расход"] > 0].sort_values("Расход", ascending=False).head(10)
    if ads.empty:
        ax.text(0.5, 0.5, "Расходы на рекламу отсутствуют", ha="center", va="center")
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["ad_performance"], figure=fig, summary="Расходов на рекламу за период нет.")

    ads["CPO"] = ads.apply(lambda r: (r["Расход"] / r["Заказы"]) if r.get("Заказы", 0) > 0 else 0.0, axis=1)

    labels = ads["Кампания"].astype(str)
    x = range(len(labels))

    ax.bar(x, ads["Расход"], color="#7c6cf6", label="Расход, ₽", alpha=0.85)
    ax.set_ylabel("Расход, ₽")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)

    ax2 = ax.twinx()
    ax2.plot(x, ads["CPO"], color="#f2677a", marker="o", linewidth=2, label="CPO, ₽")
    ax2.set_ylabel("CPO (стоимость заказа), ₽")

    fig.legend(loc="upper right", bbox_to_anchor=(0.9, 0.88))
    fig.tight_layout()

    total_spend = float(ads["Расход"].sum())
    avg_ctr = float(ads["CTR, %"].mean()) if "CTR, %" in ads.columns else 0.0
    summary = f"Кампаний: {len(ads)}. Суммарный расход: {total_spend:,.0f}₽, средний CTR: {avg_ctr:.2f}%.".replace(",", " ")
    return MetricResult(title=METRIC_LABELS["ad_performance"], figure=fig, summary=summary)


def _warehouse_stocks(start: date, end: date) -> MetricResult:
    data = build_dashboard(start, end)
    stocks = data.stocks.copy()
    fig, ax = plt.subplots(figsize=(7, 3.8))

    if stocks.empty or "Склад" not in stocks.columns or "Остаток" not in stocks.columns:
        ax.text(0.5, 0.5, "Нет данных по остаткам на складах", ha="center", va="center")
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["warehouse_stocks"], figure=fig, summary="Данных по складам нет.")

    grouped = stocks.groupby("Склад", as_index=False)["Остаток"].sum()
    grouped = grouped[grouped["Остаток"] > 0].sort_values("Остаток", ascending=False).head(12)

    if grouped.empty:
        ax.text(0.5, 0.5, "Остатки на складах равны 0", ha="center", va="center")
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["warehouse_stocks"], figure=fig, summary="Остатки на складах отсутствуют.")

    bars = ax.barh(grouped["Склад"], grouped["Остаток"], color="#3ecf8e")
    ax.set_xlabel("Остаток, шт.")
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.3)

    max_qty = max(grouped["Остаток"])
    for bar in bars:
        w = bar.get_width()
        ax.text(w + (max_qty * 0.01), bar.get_y() + bar.get_height() / 2, f"{int(w)} шт", va="center", fontsize=8)

    fig.tight_layout()
    total_wh_qty = int(grouped["Остаток"].sum())
    top_wh = str(grouped.iloc[0]["Склад"])
    top_qty = int(grouped.iloc[0]["Остаток"])
    top_pct = (top_qty / total_wh_qty * 100) if total_wh_qty > 0 else 0.0

    summary = f"Всего складов: {len(grouped)}. Топ-склад: {top_wh} ({top_qty} шт, {top_pct:.1f}% остатков)."
    return MetricResult(title=METRIC_LABELS["warehouse_stocks"], figure=fig, summary=summary)


METRIC_BUILDERS: dict[str, Callable[[date, date], MetricResult]] = {
    "sales_orders": _sales_orders,
    "ads": _ads,
    "stocks": _stocks,
    "pnl": _pnl,
    "abc_analysis": _abc_analysis,
    "buyout_rate": _buyout_rate,
    "oos_risk": _oos_risk,
    "wb_deductions": _wb_deductions,
    "ad_performance": _ad_performance,
    "warehouse_stocks": _warehouse_stocks,
}


def build_metric(code: str, start: date, end: date) -> MetricResult:
    if code not in METRIC_BUILDERS:
        raise ValueError(f"Неизвестная метрика отчёта: {code}")
    return METRIC_BUILDERS[code](start, end)
