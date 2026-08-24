"""Preset report metrics: (start, end) -> MetricResult(title, figure, summary).

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
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import pandas as pd

from calculations import build_dashboard
from db import read_table

# Branded palette adapted for print / light PDF background -- the app itself
# is dark-themed (see app.py's CSS variables), but a PDF is read/printed on
# white, so these are the same accent hues on a light surface instead.
COLOR_ACCENT = "#7c6cf6"
COLOR_ACCENT_STRONG = "#9a8bff"
COLOR_GOOD = "#3ecf8e"
COLOR_WARN = "#f2b84b"
COLOR_CRITICAL = "#f2677a"
COLOR_TEXT = "#1e293b"
COLOR_MUTED = "#64748b"
COLOR_GRID = "#e2e8f0"
COLOR_BG = "#ffffff"

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
    "unit_economics": "Юнит-экономика по товарам",
    "returns_cancellations": "Отмены и возвраты по дням",
    "organic_vs_ads": "Органика vs Реклама",
    "ad_funnel": "Воронка конверсии рекламных кампаний",
    "cost_structure": "Структура себестоимости товаров",
    "wb_commission_rate": "Эффективная ставка комиссии WB по категориям",
    "stock_in_transit": "Остаток на складе vs в пути",
    "weekday_pattern": "Заказы и отмены по дням недели",
}


@dataclass
class MetricResult:
    title: str
    figure: "plt.Figure"
    summary: str


def classify_stock_risk(days: float) -> str:
    """Maps days-of-supply to a risk bucket -- shared by the chart color
    logic and the summary text so a reader can't see a red bar labelled
    differently from a "critical" mention in the text."""
    if days < 7:
        return "critical"
    elif days <= 14:
        return "warn"
    return "good"


def get_status_color(status: str) -> str:
    mapping = {
        "critical": COLOR_CRITICAL,
        "warn": COLOR_WARN,
        "good": COLOR_GOOD,
        "accent": COLOR_ACCENT,
        "muted": COLOR_MUTED,
    }
    return mapping.get(status, COLOR_TEXT)


def _apply_chart_style(fig: plt.Figure, ax: plt.Axes, hide_top_right_spines: bool = True) -> None:
    """Единый визуальный стиль для всех графиков отчёта: светлый фон под
    печать, приглушённая сетка, убранные верхняя/правая рамки. Применяется
    во всех метриках ниже вместо разрозненных ad-hoc настроек в каждой."""
    fig.patch.set_facecolor(COLOR_BG)
    ax.set_facecolor(COLOR_BG)

    if hide_top_right_spines:
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    for spine in ax.spines.values():
        if spine.get_visible():
            spine.set_color(COLOR_GRID)
            spine.set_linewidth(0.8)

    ax.tick_params(colors=COLOR_TEXT, labelsize=8)
    ax.grid(True, color=COLOR_GRID, linestyle="--", linewidth=0.5, alpha=0.7)
    ax.xaxis.label.set_color(COLOR_TEXT)
    ax.xaxis.label.set_fontsize(8.5)
    ax.yaxis.label.set_color(COLOR_TEXT)
    ax.yaxis.label.set_fontsize(8.5)


def _style_twin_axis(ax: plt.Axes, ax2: plt.Axes) -> None:
    """Twin-axis charts (bars on ax, a line on ax2) need ax's right spine
    left alone for ax2 to render its own scale against -- factored out since
    _ads/_abc_analysis/_ad_performance all do exactly this."""
    ax.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_color(COLOR_GRID)
    ax2.tick_params(colors=COLOR_TEXT, labelsize=8)
    ax2.yaxis.label.set_color(COLOR_TEXT)
    ax2.yaxis.label.set_fontsize(8.5)


def _label_line_points(
    ax: plt.Axes,
    x_values: pd.Series | list,
    y_values: pd.Series | list,
    color: str,
    fmt: str = "{:.0f}",
) -> None:
    """Annotates data points on a line chart. If there are > 10 points, only
    labels the start, end, minimum, and maximum points to avoid clutter."""
    x_list = list(x_values)
    y_list = list(y_values)
    n = len(y_list)
    if n == 0:
        return

    if n <= 10:
        indices = set(range(n))
    else:
        indices = {0, n - 1}
        clean_y = pd.Series(y_list).dropna()
        if not clean_y.empty:
            indices.add(int(clean_y.idxmin()))
            indices.add(int(clean_y.idxmax()))

    for i in sorted(indices):
        val = y_list[i]
        if pd.isna(val):
            continue
        formatted_val = fmt.format(val).replace(",", " ")
        ax.annotate(
            formatted_val,
            (x_list[i], val),
            textcoords="offset points",
            xytext=(0, 6),
            ha="center",
            fontsize=7,
            color=color,
            fontweight="bold",
        )


def _sales_orders(start: date, end: date) -> MetricResult:
    daily = build_dashboard(start, end).daily
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.plot(daily["day"], daily["orders"], label="Заказы, шт.", color=COLOR_ACCENT, marker="o", linewidth=2, markersize=4)
    ax.plot(daily["day"], daily["sales"], label="Продажи, шт.", color=COLOR_GOOD, marker="o", linewidth=2, markersize=4)
    
    _label_line_points(ax, daily["day"], daily["orders"], COLOR_ACCENT)
    _label_line_points(ax, daily["day"], daily["sales"], COLOR_GOOD)
    ax.margins(y=0.15)

    ax.set_xlabel("Дата")
    ax.set_ylabel("Штук")
    ax.legend(frameon=True, facecolor="#f8fafc", edgecolor=COLOR_GRID, fontsize=8)
    _apply_chart_style(fig, ax)
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
    bars = ax.bar(daily["day"], daily["ad_spend"], color=COLOR_ACCENT, alpha=0.85, label="Расход, ₽")
    ax2 = ax.twinx()
    ax2.plot(daily["day"], daily["drr"], color=COLOR_CRITICAL, label="ДРР, %", marker="o", linewidth=2, markersize=4)
    
    max_spend = daily["ad_spend"].max() if not daily.empty else 0
    for bar in bars:
        h = bar.get_height()
        if h > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                h + (max_spend * 0.01),
                f"{h:,.0f}".replace(",", " "),
                ha="center", va="bottom", fontsize=6, color=COLOR_TEXT, rotation=45,
            )

    _label_line_points(ax2, daily["day"], daily["drr"], COLOR_CRITICAL, fmt="{:.1f}%")
    ax.margins(y=0.15)
    ax2.margins(y=0.15)

    ax.set_xlabel("Дата")
    ax.set_ylabel("Расход, ₽")
    ax2.set_ylabel("ДРР, %")

    _apply_chart_style(fig, ax, hide_top_right_spines=False)
    _style_twin_axis(ax, ax2)

    fig.legend(loc="upper left", bbox_to_anchor=(0.08, 0.92), frameon=True, facecolor="#f8fafc", edgecolor=COLOR_GRID, fontsize=8)
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
    _apply_chart_style(fig, ax)

    if stocks.empty:
        ax.text(0.5, 0.5, "Нет данных об остатках", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["stocks"], figure=fig, summary="Данных об остатках нет.")

    latest_snapshot = stocks["snapshot_at"].max()
    stocks = stocks[stocks["snapshot_at"] == latest_snapshot]

    catalog = read_table("products_catalog")
    grouped = stocks.groupby("nm_id", as_index=False)["quantity"].sum()
    grouped = grouped.sort_values("quantity", ascending=False).head(20)
    if not catalog.empty and "nm_id" in catalog.columns:
        grouped = grouped.merge(catalog[["nm_id", "supplier_article"]], on="nm_id", how="left")
    grouped["label"] = grouped.get("supplier_article").fillna(grouped["nm_id"].astype(str)) if "supplier_article" in grouped.columns else grouped["nm_id"].astype(str)

    bars = ax.barh(grouped["label"].astype(str), grouped["quantity"], color=COLOR_GOOD, alpha=0.9)
    ax.set_xlabel("Остаток, шт.")
    ax.invert_yaxis()

    max_q = grouped["quantity"].max() if not grouped.empty else 0
    for bar in bars:
        w = bar.get_width()
        ax.text(w + (max_q * 0.01), bar.get_y() + bar.get_height() / 2, f"{int(w):,.0f}".replace(",", " "), va="center", ha="left", fontsize=8, color=COLOR_TEXT)

    ax.margins(x=0.15)
    fig.tight_layout()
    total_qty = int(grouped["quantity"].sum())
    return MetricResult(
        title=METRIC_LABELS["stocks"], figure=fig,
        summary=f"Показаны топ-{len(grouped)} товаров, суммарный остаток по ним {total_qty} шт.",
    )


def _pnl(start: date, end: date) -> MetricResult:
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
    bar_colors = [
        COLOR_ACCENT, COLOR_CRITICAL, COLOR_WARN, COLOR_ACCENT_STRONG,
        COLOR_GOOD if profit >= 0 else COLOR_CRITICAL,
    ]

    bars = ax.barh(categories, values, color=bar_colors, alpha=0.9)
    ax.set_xlabel("Сумма, ₽")
    ax.invert_yaxis()
    _apply_chart_style(fig, ax)

    max_abs = max(abs(v) for v in values) if values else 0
    for bar in bars:
        w = bar.get_width()
        ax.text(
            w + (max_abs * 0.01 if w >= 0 else -max_abs * 0.01),
            bar.get_y() + bar.get_height() / 2,
            f"{w:,.0f} ₽".replace(",", " "),
            va="center", ha="left" if w >= 0 else "right", fontsize=8, color=COLOR_TEXT,
        )

    ax.margins(x=0.15)
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
        ax.text(0.5, 0.5, "Нет данных для ABC-анализа", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["abc_analysis"], figure=fig, summary="Нет данных для ABC-анализа.")

    df = df[df["Расчётная прибыль"] > 0].sort_values("Расчётная прибыль", ascending=False)
    if df.empty:
        ax.text(0.5, 0.5, "Прибыльные товары отсутствуют", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["abc_analysis"], figure=fig, summary="Прибыльные товары за период отсутствуют.")

    total_profit = df["Расчётная прибыль"].sum()
    df["cum_profit_pct"] = (df["Расчётная прибыль"].cumsum() / total_profit) * 100
    df["group"] = df["cum_profit_pct"].apply(lambda p: "A" if p <= 80 else ("B" if p <= 95 else "C"))
    if not df.empty:
        df.iloc[0, df.columns.get_loc("group")] = "A"

    group_counts = df["group"].value_counts().to_dict()
    cnt_a, cnt_b, cnt_c = group_counts.get("A", 0), group_counts.get("B", 0), group_counts.get("C", 0)

    top_df = df.head(15)
    labels = top_df["Артикул продавца"].fillna(top_df["Артикул WB"].astype(str)).astype(str)

    bars = ax.bar(labels, top_df["Расчётная прибыль"], color=COLOR_ACCENT, alpha=0.85, label="Прибыль, ₽")
    ax.set_ylabel("Прибыль, ₽")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)

    max_prof = top_df["Расчётная прибыль"].max() if not top_df.empty else 0
    for bar in bars:
        h = bar.get_height()
        if h > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                h + (max_prof * 0.01),
                f"{h:,.0f}".replace(",", " "),
                ha="center", va="bottom", fontsize=6.5, color=COLOR_TEXT, rotation=30,
            )

    ax2 = ax.twinx()
    ax2.plot(labels, top_df["cum_profit_pct"], color=COLOR_CRITICAL, marker="o", linewidth=2, markersize=4, label="Накопленная доля, %")
    ax2.set_ylabel("Накопленная доля, %")
    ax2.set_ylim(0, 105)
    ax2.axhline(80, color=COLOR_MUTED, linestyle="--", alpha=0.5)
    ax2.axhline(95, color=COLOR_MUTED, linestyle=":", alpha=0.5)

    _label_line_points(ax2, labels, top_df["cum_profit_pct"], COLOR_CRITICAL, fmt="{:.0f}%")
    ax.margins(y=0.2)

    _apply_chart_style(fig, ax, hide_top_right_spines=False)
    _style_twin_axis(ax, ax2)

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
        ax.text(0.5, 0.5, "Нет данных по выкупу товаров", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["buyout_rate"], figure=fig, summary="Данных по выкупу товаров нет.")

    df = df[df["Продажи, шт"] > 0].sort_values("Продажи, шт", ascending=False).head(15)
    if df.empty:
        ax.text(0.5, 0.5, "Нет продаж за указанный период", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["buyout_rate"], figure=fig, summary="Продаж за период не обнаружено.")

    df["buyout_pct"] = 100.0 - df["Возвраты, %"].clip(lower=0.0, upper=100.0)
    labels = df["Артикул продавца"].fillna(df["Артикул WB"].astype(str)).astype(str)

    bars = ax.barh(labels, df["buyout_pct"], color=COLOR_GOOD, alpha=0.9)
    ax.set_xlabel("% выкупа")
    ax.set_xlim(0, 105)
    ax.invert_yaxis()
    _apply_chart_style(fig, ax)

    for bar in bars:
        w = bar.get_width()
        ax.text(w + 1, bar.get_y() + bar.get_height() / 2, f"{w:.1f}%", va="center", fontsize=8, color=COLOR_TEXT)

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
        ax.text(0.5, 0.5, "Нет данных о днях запаса", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["oos_risk"], figure=fig, summary="Данных о днях запаса нет.")

    df = df[df["Остаток"] > 0].sort_values("Запас, дней", ascending=True).head(15)
    if df.empty:
        ax.text(0.5, 0.5, "Нет товаров на остатке", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["oos_risk"], figure=fig, summary="Товаров с остатками не найдено.")

    labels = df["Артикул продавца"].fillna(df["Артикул WB"].astype(str)).astype(str)
    bar_colors = [get_status_color(classify_stock_risk(d)) for d in df["Запас, дней"]]

    bars = ax.barh(labels, df["Запас, дней"], color=bar_colors, alpha=0.9)
    ax.axvline(7, color=COLOR_CRITICAL, linestyle="--", linewidth=1.5, label="Порог OOS (7 дн.)")
    ax.set_xlabel("Дни запаса")
    ax.invert_yaxis()
    _apply_chart_style(fig, ax)
    ax.legend(loc="lower right", frameon=True, facecolor="#f8fafc", edgecolor=COLOR_GRID, fontsize=8)

    for bar in bars:
        w = bar.get_width()
        ax.text(w + 0.5, bar.get_y() + bar.get_height() / 2, f"{w:.0f} дн", va="center", fontsize=8, color=COLOR_TEXT)

    fig.tight_layout()
    critical_cnt = int((df["Запас, дней"] < 7).sum())
    min_days = float(df["Запас, дней"].min()) if not df.empty else 0.0
    summary = f"Товаров с риском OOS (<7 дн.): {critical_cnt} шт. Минимальный запас: {min_days:.0f} дн."
    return MetricResult(title=METRIC_LABELS["oos_risk"], figure=fig, summary=summary)


def _wb_deductions(start: date, end: date) -> MetricResult:
    data = build_dashboard(start, end)
    fin = data.financial
    fig, ax = plt.subplots(figsize=(7.5, 4))

    categories = {
        "Логистика": float(fin.get("logistics", 0.0)) + float(fin.get("rebill_logistics", 0.0)),
        "Хранение": float(fin.get("storage", 0.0)),
        "Штрафы": float(fin.get("penalties", 0.0)),
        "Прочие удержания": float(fin.get("deductions", 0.0)),
        "Платная приёмка": float(fin.get("acceptance", 0.0)),
    }
    items = {k: v for k, v in categories.items() if v > 0}

    if not items:
        ax.text(0.5, 0.5, "Удержания WB отсутствуют", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["wb_deductions"], figure=fig, summary="Удержаний WB за период нет.")

    labels = list(items.keys())
    values = list(items.values())
    pie_colors = [COLOR_ACCENT, "#38bdf8", COLOR_WARN, COLOR_CRITICAL, COLOR_GOOD][: len(values)]
    total_exp = sum(values)

    wedges, _texts, autotexts = ax.pie(
        values,
        autopct=lambda pct: f"{pct:.0f}%\n({pct / 100 * total_exp:,.0f}₽)".replace(",", " ") if pct >= 3 else "",
        startangle=140,
        colors=pie_colors,
        wedgeprops=dict(width=0.4, edgecolor="w", linewidth=1.5),
    )
    plt.setp(autotexts, size=8, weight="bold", color="#ffffff", path_effects=[pe.withStroke(linewidth=2, foreground="#000000")])
    ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8, frameon=True, facecolor="#f8fafc", edgecolor=COLOR_GRID)

    fig.patch.set_facecolor(COLOR_BG)
    ax.set_facecolor(COLOR_BG)

    fig.tight_layout()
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
        ax.text(0.5, 0.5, "Нет данных по рекламным кампаниям", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["ad_performance"], figure=fig, summary="Данных по рекламе нет.")

    ads = ads[ads["Расход"] > 0].sort_values("Расход", ascending=False).head(10)
    if ads.empty:
        ax.text(0.5, 0.5, "Расходы на рекламу отсутствуют", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["ad_performance"], figure=fig, summary="Расходов на рекламу за период нет.")

    ads["CPO"] = ads.apply(lambda r: (r["Расход"] / r["Заказы"]) if r.get("Заказы", 0) > 0 else 0.0, axis=1)

    labels = ads["Кампания"].astype(str)
    x = range(len(labels))

    bars = ax.bar(x, ads["Расход"], color=COLOR_ACCENT, alpha=0.85, label="Расход, ₽")
    ax.set_ylabel("Расход, ₽")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)

    max_spend = ads["Расход"].max() if not ads.empty else 0
    for bar in bars:
        h = bar.get_height()
        if h > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                h + (max_spend * 0.01),
                f"{h:,.0f}₽".replace(",", " "),
                ha="center", va="bottom", fontsize=7, color=COLOR_TEXT,
            )

    ax2 = ax.twinx()
    ax2.plot(x, ads["CPO"], color=COLOR_CRITICAL, marker="o", linewidth=2, markersize=4, label="CPO, ₽")
    ax2.set_ylabel("CPO (стоимость заказа), ₽")

    _label_line_points(ax2, list(x), ads["CPO"], COLOR_CRITICAL, fmt="{:.0f}₽")
    ax.margins(y=0.15)
    ax2.margins(y=0.15)

    _apply_chart_style(fig, ax, hide_top_right_spines=False)
    _style_twin_axis(ax, ax2)

    fig.legend(loc="upper right", bbox_to_anchor=(0.9, 0.88), frameon=True, facecolor="#f8fafc", edgecolor=COLOR_GRID, fontsize=8)
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
        ax.text(0.5, 0.5, "Нет данных по остаткам на складах", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["warehouse_stocks"], figure=fig, summary="Данных по складам нет.")

    grouped = stocks.groupby("Склад", as_index=False)["Остаток"].sum()
    grouped = grouped[grouped["Остаток"] > 0].sort_values("Остаток", ascending=False).head(12)

    if grouped.empty:
        ax.text(0.5, 0.5, "Остатки на складах равны 0", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["warehouse_stocks"], figure=fig, summary="Остатки на складах отсутствуют.")

    bars = ax.barh(grouped["Склад"], grouped["Остаток"], color=COLOR_GOOD, alpha=0.9)
    ax.set_xlabel("Остаток, шт.")
    ax.invert_yaxis()
    _apply_chart_style(fig, ax)

    max_qty = max(grouped["Остаток"])
    for bar in bars:
        w = bar.get_width()
        ax.text(w + (max_qty * 0.01), bar.get_y() + bar.get_height() / 2, f"{int(w)} шт", va="center", fontsize=8, color=COLOR_TEXT)

    fig.tight_layout()
    total_wh_qty = int(grouped["Остаток"].sum())
    top_wh = str(grouped.iloc[0]["Склад"])
    top_qty = int(grouped.iloc[0]["Остаток"])
    top_pct = (top_qty / total_wh_qty * 100) if total_wh_qty > 0 else 0.0

    summary = f"Всего складов: {len(grouped)}. Топ-склад: {top_wh} ({top_qty} шт, {top_pct:.1f}% остатков)."
    return MetricResult(title=METRIC_LABELS["warehouse_stocks"], figure=fig, summary=summary)


def _unit_economics(start: date, end: date) -> MetricResult:
    data = build_dashboard(start, end)
    df = data.financial_products.copy()
    fig, ax = plt.subplots(figsize=(7, 3.8))

    if df.empty or "Маржинальность, %" not in df.columns:
        ax.text(0.5, 0.5, "Нет данных для юнит-экономики", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["unit_economics"], figure=fig, summary="Нет данных для юнит-экономики.")

    df = df[df["Продано нетто"] > 0].sort_values("Маржинальность, %", ascending=False).head(15)
    if df.empty:
        ax.text(0.5, 0.5, "Нет проданных товаров за период", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["unit_economics"], figure=fig, summary="Проданных товаров за период не обнаружено.")

    labels = df["Артикул продавца"].fillna(df["Артикул WB"].astype(str)).astype(str)
    bar_colors = [COLOR_GOOD if v >= 0 else COLOR_CRITICAL for v in df["Маржинальность, %"]]

    bars = ax.barh(labels, df["Маржинальность, %"], color=bar_colors, alpha=0.9)
    ax.set_xlabel("Маржинальность, %")
    ax.invert_yaxis()
    _apply_chart_style(fig, ax)

    for bar in bars:
        w = bar.get_width()
        ax.text(
            w + (1 if w >= 0 else -1), bar.get_y() + bar.get_height() / 2, f"{w:.1f}%",
            va="center", ha="left" if w >= 0 else "right", fontsize=8, color=COLOR_TEXT,
        )

    fig.tight_layout()
    avg_margin = float(df["Маржинальность, %"].mean())
    top_label = labels.iloc[0]
    top_margin = float(df.iloc[0]["Маржинальность, %"])
    summary = f"Средняя маржинальность топ-{len(df)}: {avg_margin:.1f}%. Лучший товар: {top_label} ({top_margin:.1f}%)."
    return MetricResult(title=METRIC_LABELS["unit_economics"], figure=fig, summary=summary)


def _returns_cancellations(start: date, end: date) -> MetricResult:
    orders = read_table("orders")
    sales = read_table("sales")
    fig, ax = plt.subplots(figsize=(7, 3.5))
    _apply_chart_style(fig, ax)

    if orders.empty and sales.empty:
        ax.text(0.5, 0.5, "Нет данных об отменах и возвратах", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["returns_cancellations"], figure=fig, summary="Данных об отменах и возвратах нет.")

    all_days = pd.date_range(start, end).date

    cancel_series = pd.Series(0, index=all_days)
    if not orders.empty:
        orders = orders.copy()
        orders["order_date"] = pd.to_datetime(orders["order_date"], errors="coerce").dt.date
        period_orders = orders[(orders["order_date"] >= start) & (orders["order_date"] <= end)]
        daily_cancel = period_orders[period_orders["is_cancel"] == 1].groupby("order_date").size()
        cancel_series = daily_cancel.reindex(all_days, fill_value=0)

    return_series = pd.Series(0, index=all_days)
    if not sales.empty:
        sales = sales.copy()
        sales["sale_date"] = pd.to_datetime(sales["sale_date"], errors="coerce").dt.date
        period_sales = sales[(sales["sale_date"] >= start) & (sales["sale_date"] <= end)]
        daily_return = period_sales[period_sales["is_return"] == 1].groupby("sale_date").size()
        return_series = daily_return.reindex(all_days, fill_value=0)

    ax.plot(all_days, cancel_series.values, label="Отмены, шт.", color=COLOR_CRITICAL, marker="o", linewidth=2, markersize=4)
    ax.plot(all_days, return_series.values, label="Возвраты, шт.", color=COLOR_WARN, marker="o", linewidth=2, markersize=4)

    _label_line_points(ax, all_days, cancel_series.values, COLOR_CRITICAL)
    _label_line_points(ax, all_days, return_series.values, COLOR_WARN)
    ax.margins(y=0.15)

    ax.set_xlabel("Дата")
    ax.set_ylabel("Штук")
    ax.legend(frameon=True, facecolor="#f8fafc", edgecolor=COLOR_GRID, fontsize=8)
    fig.autofmt_xdate()

    total_cancel = int(cancel_series.sum())
    total_return = int(return_series.sum())
    summary = f"Итого за период: отмен {total_cancel} шт., возвратов {total_return} шт."
    return MetricResult(title=METRIC_LABELS["returns_cancellations"], figure=fig, summary=summary)


def _organic_vs_ads(start: date, end: date) -> MetricResult:
    data = build_dashboard(start, end)
    daily = data.daily
    ads = data.ads.copy()
    fig, ax = plt.subplots(figsize=(7.5, 4))

    total_orders = int(daily["orders"].sum()) if not daily.empty else 0
    if total_orders == 0:
        ax.text(0.5, 0.5, "Заказов за период не было", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["organic_vs_ads"], figure=fig, summary="Заказов за период не было.")

    ad_orders_raw = int(ads["Заказы"].sum()) if not ads.empty and "Заказы" in ads.columns else 0
    ad_orders = min(ad_orders_raw, total_orders)
    organic_orders = total_orders - ad_orders

    labels = ["Органика", "Реклама"]
    values = [organic_orders, ad_orders]
    pie_colors = [COLOR_GOOD, COLOR_ACCENT]

    wedges, _texts, autotexts = ax.pie(
        values,
        autopct=lambda pct: f"{pct:.0f}%\n({pct / 100 * total_orders:,.0f} шт.)".replace(",", " ") if pct > 0 else "",
        startangle=140,
        colors=pie_colors,
        wedgeprops=dict(width=0.4, edgecolor="w", linewidth=1.5),
    )
    plt.setp(autotexts, size=8, weight="bold", color="#ffffff", path_effects=[pe.withStroke(linewidth=2, foreground="#000000")])
    ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8, frameon=True, facecolor="#f8fafc", edgecolor=COLOR_GRID)

    fig.patch.set_facecolor(COLOR_BG)
    ax.set_facecolor(COLOR_BG)
    fig.tight_layout()

    ad_share = (ad_orders / total_orders * 100) if total_orders else 0.0
    summary = f"Всего заказов: {total_orders} шт. Из рекламы: {ad_orders} шт. ({ad_share:.1f}%), органика: {organic_orders} шт."
    return MetricResult(title=METRIC_LABELS["organic_vs_ads"], figure=fig, summary=summary)


def _ad_funnel(start: date, end: date) -> MetricResult:
    ads_daily = read_table("ads_daily")
    fig, ax = plt.subplots(figsize=(7, 3.5))

    if ads_daily.empty or "day" not in ads_daily.columns:
        ax.text(0.5, 0.5, "Нет данных по рекламным кампаниям", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["ad_funnel"], figure=fig, summary="Данных по рекламе за период нет.")

    ads_daily = ads_daily.copy()
    ads_daily["day_dt"] = pd.to_datetime(ads_daily["day"], errors="coerce").dt.date
    period_ads = ads_daily[(ads_daily["day_dt"] >= start) & (ads_daily["day_dt"] <= end)]

    views = int(period_ads["views"].sum()) if "views" in period_ads.columns else 0
    clicks = int(period_ads["clicks"].sum()) if "clicks" in period_ads.columns else 0
    atbs = int(period_ads["atbs"].sum()) if "atbs" in period_ads.columns else 0
    orders_count = int(period_ads["orders_count"].sum()) if "orders_count" in period_ads.columns else 0

    if period_ads.empty or (views == 0 and clicks == 0 and atbs == 0 and orders_count == 0):
        ax.text(0.5, 0.5, "Нет данных по рекламным кампаниям", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["ad_funnel"], figure=fig, summary="Данных по рекламе за период нет.")

    labels = ["Показы", "Клики", "В корзину", "Заказы"]
    values = [views, clicks, atbs, orders_count]

    bars = ax.barh(labels, values, color=COLOR_ACCENT, alpha=0.85)
    ax.invert_yaxis()
    ax.set_xlabel("Количество")
    _apply_chart_style(fig, ax)

    max_val = max(values) if values else 0
    for bar in bars:
        w = bar.get_width()
        ax.text(
            w + (max_val * 0.01 if max_val > 0 else 0.1), bar.get_y() + bar.get_height() / 2,
            f"{int(w):,.0f}".replace(",", " "), va="center", fontsize=8, color=COLOR_TEXT
        )

    fig.tight_layout()
    ctr = (clicks / views * 100.0) if views > 0 else 0.0
    cr_order = (orders_count / clicks * 100.0) if clicks > 0 else 0.0
    summary = f"Показов: {views}, кликов: {clicks}, заказов: {orders_count}. Конверсия CTR: {ctr:.2f}%, клик->заказ: {cr_order:.2f}%."
    return MetricResult(title=METRIC_LABELS["ad_funnel"], figure=fig, summary=summary)


def _cost_structure(start: date, end: date) -> MetricResult:
    costs = read_table("costs")
    fig, ax = plt.subplots(figsize=(7, 3.8))

    if costs.empty:
        ax.text(0.5, 0.5, "Нет данных о себестоимости товаров", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["cost_structure"], figure=fig, summary="Данных о себестоимости товаров нет.")

    df = costs.copy()
    df["material"] = pd.to_numeric(df["material_cost_rub"], errors="coerce").fillna(0.0) if "material_cost_rub" in df.columns else 0.0
    df["packaging"] = pd.to_numeric(df["packaging_cost_rub"], errors="coerce").fillna(0.0) if "packaging_cost_rub" in df.columns else 0.0
    df["labor"] = pd.to_numeric(df["labor_cost_rub"], errors="coerce").fillna(0.0) if "labor_cost_rub" in df.columns else 0.0
    df["other"] = pd.to_numeric(df["other_cost_rub"], errors="coerce").fillna(0.0) if "other_cost_rub" in df.columns else 0.0
    df["total_cost"] = df["material"] + df["packaging"] + df["labor"] + df["other"]

    df = df[df["total_cost"] > 0].sort_values("total_cost", ascending=False).head(10)
    if df.empty:
        ax.text(0.5, 0.5, "Нет данных о себестоимости товаров", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["cost_structure"], figure=fig, summary="Данных о себестоимости товаров нет.")

    labels = df.get("supplier_article").fillna(df["nm_id"].astype(str)) if "supplier_article" in df.columns else df["nm_id"].astype(str)
    labels = labels.astype(str)

    ax.barh(labels, df["material"], color=COLOR_ACCENT, label="Материалы", alpha=0.85)
    ax.barh(labels, df["packaging"], left=df["material"], color=COLOR_ACCENT_STRONG, label="Упаковка", alpha=0.85)
    ax.barh(labels, df["labor"], left=df["material"] + df["packaging"], color=COLOR_GOOD, label="Труд", alpha=0.85)
    ax.barh(labels, df["other"], left=df["material"] + df["packaging"] + df["labor"], color=COLOR_WARN, label="Прочее", alpha=0.85)

    max_total = df["total_cost"].max() if not df.empty else 1.0
    categories_cols = ["material", "packaging", "labor", "other"]
    for idx, row in df.reset_index(drop=True).iterrows():
        cum = 0.0
        for col in categories_cols:
            val = row[col]
            if val > max_total * 0.05:
                ax.text(
                    cum + val / 2, idx, f"{val:,.0f}₽".replace(",", " "),
                    va="center", ha="center", fontsize=7, color="#ffffff", fontweight="bold",
                    path_effects=[pe.withStroke(linewidth=1.5, foreground="#000000")],
                )
            cum += val

    ax.set_xlabel("Себестоимость, ₽")
    ax.invert_yaxis()
    _apply_chart_style(fig, ax)
    ax.legend(frameon=True, facecolor="#f8fafc", edgecolor=COLOR_GRID, fontsize=8)
    fig.tight_layout()

    total_mat = df["material"].sum()
    total_all = df["total_cost"].sum()
    mat_pct = (total_mat / total_all * 100.0) if total_all > 0 else 0.0

    summary = f"Средняя доля материалов в себестоимости по топ-{len(df)} товарам: {mat_pct:.1f}%."
    return MetricResult(title=METRIC_LABELS["cost_structure"], figure=fig, summary=summary)


def _wb_commission_rate(start: date, end: date) -> MetricResult:
    fin = read_table("financial_report")
    fig, ax = plt.subplots(figsize=(7, 3.8))

    if fin.empty or "operation_date" not in fin.columns:
        ax.text(0.5, 0.5, "Нет данных отчёта реализации", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["wb_commission_rate"], figure=fig, summary="Данных отчёта реализации за период нет.")

    fin = fin.copy()
    fin["op_dt"] = pd.to_datetime(fin["operation_date"], errors="coerce").dt.date
    period_fin = fin[(fin["op_dt"] >= start) & (fin["op_dt"] <= end)].copy()

    if period_fin.empty:
        ax.text(0.5, 0.5, "Нет данных отчёта реализации за период", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["wb_commission_rate"], figure=fig, summary="Данных отчёта реализации за период нет.")

    catalog = read_table("products_catalog")
    if not catalog.empty and "nm_id" in catalog.columns and "subject_name" in catalog.columns:
        period_fin = period_fin.merge(catalog[["nm_id", "subject_name"]], on="nm_id", how="left")

    if "subject_name" not in period_fin.columns:
        period_fin["subject_name"] = "Без категории"
    else:
        period_fin["subject_name"] = period_fin["subject_name"].fillna("Без категории")

    period_fin["comm"] = pd.to_numeric(period_fin["commission"], errors="coerce").fillna(0.0) if "commission" in period_fin.columns else 0.0
    period_fin["acq"] = pd.to_numeric(period_fin["acquiring_fee"], errors="coerce").fillna(0.0) if "acquiring_fee" in period_fin.columns else 0.0
    period_fin["retail"] = pd.to_numeric(period_fin["retail_amount"], errors="coerce").fillna(0.0) if "retail_amount" in period_fin.columns else 0.0

    grouped = period_fin.groupby("subject_name", as_index=False).agg({
        "comm": "sum",
        "acq": "sum",
        "retail": "sum",
    })

    grouped = grouped[grouped["retail"] > 0].copy()
    if grouped.empty:
        ax.text(0.5, 0.5, "Нет данных с продажами в отчёте реализации", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["wb_commission_rate"], figure=fig, summary="Данных отчёта реализации за период нет.")

    grouped["eff_rate"] = ((grouped["comm"] + grouped["acq"]) / grouped["retail"]) * 100.0
    grouped = grouped.sort_values("retail", ascending=False).head(10)

    bars = ax.barh(grouped["subject_name"].astype(str), grouped["eff_rate"], color=COLOR_ACCENT, alpha=0.85)
    ax.set_xlabel("Эффективная ставка комиссии, %")
    ax.invert_yaxis()
    _apply_chart_style(fig, ax)

    max_val = max(grouped["eff_rate"]) if not grouped.empty else 0
    for bar in bars:
        w = bar.get_width()
        ax.text(w + (max_val * 0.01 if max_val > 0 else 0.1), bar.get_y() + bar.get_height() / 2, f"{w:.1f}%", va="center", fontsize=8, color=COLOR_TEXT)

    fig.tight_layout()
    top_cat_row = grouped.loc[grouped["eff_rate"].idxmax()]
    top_cat = str(top_cat_row["subject_name"])
    top_rate = float(top_cat_row["eff_rate"])
    summary = f"Категория с максимальной эффективной комиссией: {top_cat} ({top_rate:.1f}%)."
    return MetricResult(title=METRIC_LABELS["wb_commission_rate"], figure=fig, summary=summary)


def _stock_in_transit(start: date, end: date) -> MetricResult:
    stocks = read_table("stocks")
    fig, ax = plt.subplots(figsize=(7, 3.8))

    if stocks.empty or "snapshot_at" not in stocks.columns or "warehouse_name" not in stocks.columns:
        ax.text(0.5, 0.5, "Нет данных об остатках и товарах в пути", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["stock_in_transit"], figure=fig, summary="Данных об остатках на складах нет.")

    latest_snapshot = stocks["snapshot_at"].max()
    stocks = stocks[stocks["snapshot_at"] == latest_snapshot].copy()

    stocks["quantity"] = pd.to_numeric(stocks["quantity"], errors="coerce").fillna(0.0) if "quantity" in stocks.columns else 0.0
    stocks["in_way_to_client"] = pd.to_numeric(stocks["in_way_to_client"], errors="coerce").fillna(0.0) if "in_way_to_client" in stocks.columns else 0.0
    stocks["in_way_from_client"] = pd.to_numeric(stocks["in_way_from_client"], errors="coerce").fillna(0.0) if "in_way_from_client" in stocks.columns else 0.0

    grouped = stocks.groupby("warehouse_name", as_index=False).agg({
        "quantity": "sum",
        "in_way_to_client": "sum",
        "in_way_from_client": "sum",
    })

    grouped["total_item_qty"] = grouped["quantity"] + grouped["in_way_to_client"] + grouped["in_way_from_client"]
    grouped = grouped[grouped["total_item_qty"] > 0].sort_values("quantity", ascending=False).head(10)

    if grouped.empty:
        ax.text(0.5, 0.5, "Остатки и товары в пути равны 0", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["stock_in_transit"], figure=fig, summary="Данных об остатках на складах нет.")

    wh_labels = grouped["warehouse_name"].astype(str)
    ax.barh(wh_labels, grouped["quantity"], color=COLOR_GOOD, label="На складе", alpha=0.85)
    ax.barh(wh_labels, grouped["in_way_to_client"], left=grouped["quantity"], color=COLOR_ACCENT, label="В пути к клиенту", alpha=0.85)
    ax.barh(wh_labels, grouped["in_way_from_client"], left=grouped["quantity"] + grouped["in_way_to_client"], color=COLOR_WARN, label="Возврат от клиента", alpha=0.85)

    max_total = grouped["total_item_qty"].max() if not grouped.empty else 1.0
    for idx, row in grouped.reset_index(drop=True).iterrows():
        cum = 0.0
        for col in ["quantity", "in_way_to_client", "in_way_from_client"]:
            val = row[col]
            if val > max_total * 0.05:
                ax.text(
                    cum + val / 2, idx, f"{int(val):,.0f}".replace(",", " "),
                    va="center", ha="center", fontsize=7, color="#ffffff", fontweight="bold",
                    path_effects=[pe.withStroke(linewidth=1.5, foreground="#000000")],
                )
            cum += val

    ax.set_xlabel("Количество, шт.")
    ax.invert_yaxis()
    _apply_chart_style(fig, ax)
    ax.legend(frameon=True, facecolor="#f8fafc", edgecolor=COLOR_GRID, fontsize=8)
    fig.tight_layout()

    total_transit = int(stocks["in_way_to_client"].sum() + stocks["in_way_from_client"].sum())
    total_all = int(stocks["quantity"].sum() + total_transit)
    transit_pct = (total_transit / total_all * 100.0) if total_all > 0 else 0.0

    summary = f"В пути (клиенту + от клиента): {total_transit} шт. ({transit_pct:.1f}% от общего объёма)."
    return MetricResult(title=METRIC_LABELS["stock_in_transit"], figure=fig, summary=summary)


def _weekday_pattern(start: date, end: date) -> MetricResult:
    orders = read_table("orders")
    fig, ax = plt.subplots(figsize=(7, 3.5))

    if orders.empty or "order_date" not in orders.columns:
        ax.text(0.5, 0.5, "Нет данных по заказам за период", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["weekday_pattern"], figure=fig, summary="Данных о заказах за период нет.")

    orders = orders.copy()
    orders["order_dt"] = pd.to_datetime(orders["order_date"], errors="coerce")
    period_orders = orders[(orders["order_dt"].dt.date >= start) & (orders["order_dt"].dt.date <= end)].copy()

    if period_orders.empty:
        ax.text(0.5, 0.5, "Нет данных по заказам за период", ha="center", va="center", color=COLOR_MUTED, fontsize=9)
        ax.axis("off")
        return MetricResult(title=METRIC_LABELS["weekday_pattern"], figure=fig, summary="Данных о заказах за период нет.")

    period_orders["weekday"] = period_orders["order_dt"].dt.dayofweek
    days_labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    total_orders = period_orders.groupby("weekday").size().reindex(range(7), fill_value=0)

    if "is_cancel" in period_orders.columns:
        cancel_orders = period_orders[period_orders["is_cancel"] == 1].groupby("weekday").size().reindex(range(7), fill_value=0)
    else:
        cancel_orders = pd.Series(0, index=range(7))

    x = range(7)
    width = 0.35
    bars1 = ax.bar([i - width/2 for i in x], total_orders.values, width, label="Всего заказов", color=COLOR_ACCENT, alpha=0.85)
    bars2 = ax.bar([i + width/2 for i in x], cancel_orders.values, width, label="Отмены", color=COLOR_CRITICAL, alpha=0.85)

    max_val = max(total_orders.max(), cancel_orders.max(), 1)
    for bar in bars1:
        h = bar.get_height()
        if h > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                h + (max_val * 0.01),
                f"{int(h)}",
                ha="center", va="bottom", fontsize=7, color=COLOR_TEXT,
            )
    for bar in bars2:
        h = bar.get_height()
        if h > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                h + (max_val * 0.01),
                f"{int(h)}",
                ha="center", va="bottom", fontsize=7, color=COLOR_TEXT,
            )

    ax.margins(y=0.15)
    ax.set_xticks(list(x))
    ax.set_xticklabels(days_labels, fontsize=8)
    ax.set_ylabel("Штук")

    _apply_chart_style(fig, ax)
    ax.legend(frameon=True, facecolor="#f8fafc", edgecolor=COLOR_GRID, fontsize=8)
    fig.tight_layout()

    max_orders_idx = int(total_orders.values.argmax())
    max_orders_day = days_labels[max_orders_idx]
    max_orders_cnt = int(total_orders.iloc[max_orders_idx])

    cancel_rates = [(c / t * 100.0) if t > 0 else 0.0 for c, t in zip(cancel_orders.values, total_orders.values)]
    max_cancel_idx = int(pd.Series(cancel_rates).idxmax()) if any(r > 0 for r in cancel_rates) else 0
    max_cancel_day = days_labels[max_cancel_idx]
    max_cancel_rate = cancel_rates[max_cancel_idx]

    summary = f"Пик заказов: {max_orders_day} ({max_orders_cnt} шт.). Макс. % отмен: {max_cancel_day} ({max_cancel_rate:.1f}%)."
    return MetricResult(title=METRIC_LABELS["weekday_pattern"], figure=fig, summary=summary)


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
    "unit_economics": _unit_economics,
    "returns_cancellations": _returns_cancellations,
    "organic_vs_ads": _organic_vs_ads,
    "ad_funnel": _ad_funnel,
    "cost_structure": _cost_structure,
    "wb_commission_rate": _wb_commission_rate,
    "stock_in_transit": _stock_in_transit,
    "weekday_pattern": _weekday_pattern,
}


def build_metric(code: str, start: date, end: date) -> MetricResult:
    if code not in METRIC_BUILDERS:
        raise ValueError(f"Неизвестная метрика отчёта: {code}")
    return METRIC_BUILDERS[code](start, end)
