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

from ui_helpers import (
    money, num, pct,
    infer_material_name, material_key, ceil_to_batch, kpi_card,
    render_problem_products_panel,
    _parse_local_datetime, _quality_row, _normalize_supplier_article,
    _positive_int_set, _cost_coverage_diagnostics, build_data_quality_overview,
    _article_margin_signal, _decision_center_recommendation,
    build_article_margin_view, procurement_recommendations,
    build_consolidated_purchase_plan,
)


def render(ctx: dict) -> None:
    data = ctx['data']
    period = ctx['period']

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
        )
    with cols[0]: kpi_card("Заказы", num(data.kpi["orders"]), money(data.kpi["order_amount"]))
    with cols[1]: kpi_card("Выкупы", num(data.kpi["sales"]), f"Оперативный API · {pct(data.kpi['buyout'])}")
    with cols[2]: kpi_card("Возвраты", num(data.kpi["returns"]), "За выбранный период")
    with cols[3]: kpi_card("Выручка", money(data.kpi["revenue"]), "Оперативные данные")
    with cols[4]: kpi_card("Реклама", money(data.kpi["ad_spend"]), f"ДРР {pct(data.kpi['drr'])}")
    with cols[5]: kpi_card("Остаток", num(data.kpi["stock"]), "На складах WB")

    st.caption("Оперативные заказы и выкупы могут обновляться с задержкой и временно отличаться от главной страницы кабинета WB. Для бухгалтерской прибыли используйте раздел «Финансы».")
    st.markdown("### Динамика")
    chart_df = data.hourly.copy() if period == "Сегодня" else data.daily.copy()
    if period == "Сегодня":
        chart_df["hour"] = chart_df["hour"].map(lambda h: f"{int(h):02d}:00")
    fig = px.line(chart_df, x=("hour" if period == "Сегодня" else "day"), y=(["order_amount", "revenue"] if period == "Сегодня" else ["order_amount", "revenue", "ad_spend"]), markers=False)
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
        st.markdown("### Требует внимания")
        render_problem_products_panel(data.financial_products, data.alerts)
    with c2:
        st.markdown("### Товары")
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
    st.markdown('<div class="warning-box">Текущая маржа — оценочная и пока не учитывает всю детализацию комиссий, логистики, хранения и удержаний WB. Эти статьи будут подключены отдельным финансовым модулем.</div>', unsafe_allow_html=True)

