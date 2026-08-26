from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
import hashlib
import math
import time
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st

from calculations import build_dashboard
from ui_helpers import (
    money, num, pct, delta_pct,
    infer_material_name, material_key, ceil_to_batch, kpi_card, kpi_card_with_sparkline,
    render_problem_products_panel, render_funnel_bars,
    _parse_local_datetime, _quality_row, _normalize_supplier_article,
    _positive_int_set, _cost_coverage_diagnostics, build_data_quality_overview,
    _article_margin_signal, _decision_center_recommendation,
    build_article_margin_view, procurement_recommendations,
    build_consolidated_purchase_plan, render_section_header,
)


def render(ctx: dict) -> None:
    data = ctx['data']
    period = ctx['period']
    start, end = ctx['start'], ctx['end']

    # vs-previous-period trend badges on the KPI cards (see kpi_card's `delta`
    # param) need a second, equal-length window immediately before the
    # current one -- e.g. "30 дней" compares to the 30 days before that.
    prev_length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=prev_length - 1)
    prev_data = build_dashboard(prev_start, prev_end) if prev_end >= prev_start else None
    prev_kpi = prev_data.kpi if prev_data is not None else {}
    prev_profit = prev_data.financial.get("product_allocated_profit") if prev_data is not None else None

    # F-pattern: the single most consequential number (profit) sits large in the
    # top-left, where a scanning eye lands first; the rest of the operational KPIs
    # trail off to the right in decreasing importance.
    hero_col, *cols = st.columns([1.4, 1, 1, 1, 1, 1, 1])
    with hero_col:
        kpi_card(
            "Прибыль по товарам (оценка)",
            money(data.financial.get("product_allocated_profit", 0.0)),
            "Расчётная, за период · точная цифра — в «Финансы»",
            hero=True,
            delta=delta_pct(data.financial.get("product_allocated_profit", 0.0), prev_profit),
        )
    daily_trend = data.daily
    with cols[0]: kpi_card_with_sparkline("Заказы", num(data.kpi["orders"]), money(data.kpi["order_amount"]), daily_trend.get("orders"), key="spark_orders", delta=delta_pct(data.kpi["orders"], prev_kpi.get("orders")))
    with cols[1]: kpi_card_with_sparkline("Выкупы", num(data.kpi["sales"]), f"Оперативный API · {pct(data.kpi['buyout'])}", daily_trend.get("sales"), key="spark_sales", delta=delta_pct(data.kpi["sales"], prev_kpi.get("sales")))
    with cols[2]: kpi_card("Возвраты", num(data.kpi["returns"]), "За выбранный период", delta=delta_pct(data.kpi["returns"], prev_kpi.get("returns")))
    with cols[3]: kpi_card_with_sparkline("Выручка", money(data.kpi["revenue"]), "Оперативные данные", daily_trend.get("revenue"), key="spark_revenue", delta=delta_pct(data.kpi["revenue"], prev_kpi.get("revenue")))
    with cols[4]: kpi_card_with_sparkline("Реклама", money(data.kpi["ad_spend"]), f"ДРР {pct(data.kpi['drr'])}", daily_trend.get("ad_spend"), key="spark_ad_spend", color="#f2b84b", delta=delta_pct(data.kpi["ad_spend"], prev_kpi.get("ad_spend")))
    with cols[5]: kpi_card("Остаток", num(data.kpi["stock"]), "На складах WB")

    st.caption(f"Относительно предыдущего периода той же длины ({prev_start:%d.%m}–{prev_end:%d.%m})." if prev_data is not None else "")
    st.caption("Оперативные заказы и выкупы могут обновляться с задержкой и временно отличаться от главной страницы кабинета WB. Для бухгалтерской прибыли используйте раздел «Финансы».")

    render_section_header(
        "Воронка заказов",
        "Сколько заказано, сколько выкуплено и сколько возвращено за выбранный период.",
    )
    render_funnel_bars([
        ("Заказано", data.kpi["orders"], "#7c6cf6"),
        ("Выкуплено", data.kpi["sales"], "#3ecf8e"),
        ("Возврат", data.kpi["returns"], "#f2677a"),
    ])
    render_section_header(
        "Динамика",
        "Сумма заказов, выручка и расходы на рекламу по дням, с пунктирным наложением "
        "предыдущего периода той же длины для сравнения.",
    )
    chart_df = data.hourly.copy() if period == "Сегодня" else data.daily.copy()
    if period == "Сегодня":
        chart_df["hour"] = chart_df["hour"].map(lambda h: f"{int(h):02d}:00")
    fig = px.line(chart_df, x=("hour" if period == "Сегодня" else "day"), y=(["order_amount", "revenue"] if period == "Сегодня" else ["order_amount", "revenue", "ad_spend"]), markers=False)
    # Faint dashed overlay of the previous equal-length period's revenue,
    # aligned by position (day 1 vs. day 1 of the prior window) rather than
    # calendar date -- borrowed from Airzon Agency's dark-mode analytics
    # dashboard reference, using the prev_data already fetched for the KPI
    # trend badges above.
    prev_chart_df = (prev_data.hourly if period == "Сегодня" else prev_data.daily) if prev_data is not None else None
    if prev_chart_df is not None and not prev_chart_df.empty and len(prev_chart_df) == len(chart_df):
        fig.add_scatter(
            x=chart_df["hour" if period == "Сегодня" else "day"],
            y=prev_chart_df["revenue"].values,
            mode="lines", name="Выручка (пред. период)",
            line=dict(color="#8d94aa", dash="dot", width=1.5),
        )
    fig.update_layout(
        height=390,
        margin=dict(l=10, r=10, t=20, b=10),
        legend_title_text="",
        xaxis_title="",
        yaxis_title="₽",
        hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.for_each_trace(lambda t: t.update(name={"order_amount":"Сумма заказов","revenue":"Выручка","ad_spend":"Реклама"}.get(t.name,t.name)))
    st.plotly_chart(fig, use_container_width=True)

    c1, c2 = st.columns([1.2, 1.8])
    with c1:
        render_section_header(
            "Требует внимания",
            "Товары с убытком или аномалией за период — с кратким объяснением причины и что "
            "с этим можно сделать.",
        )
        render_problem_products_panel(data.financial_products, data.alerts)
    with c2:
        render_section_header(
            "Товары",
            "Заказы, продажи, выручка, ДРР, остаток и запас в днях по каждому артикулу за период.",
        )
        view = data.products[["Артикул WB", "Артикул продавца", "Заказы", "Продажи", "Выручка", "ДРР, %", "Остаток", "Запас, дней"]].copy() if not data.products.empty else data.products
        st.dataframe(
            view,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Выручка": st.column_config.NumberColumn(format="%.0f ₽"),
                "ДРР, %": st.column_config.NumberColumn(format="%.1f%%"),
                "Запас, дней": st.column_config.NumberColumn(format="%.1f"),
            },
        )

