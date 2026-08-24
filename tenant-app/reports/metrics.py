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


METRIC_BUILDERS: dict[str, Callable[[date, date], MetricResult]] = {
    "sales_orders": _sales_orders,
    "ads": _ads,
    "stocks": _stocks,
}


def build_metric(code: str, start: date, end: date) -> MetricResult:
    if code not in METRIC_BUILDERS:
        raise ValueError(f"Неизвестная метрика отчёта: {code}")
    return METRIC_BUILDERS[code](start, end)
