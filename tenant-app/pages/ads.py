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
    infer_material_name, material_key, ceil_to_batch, kpi_card, render_empty_state,
    _parse_local_datetime, _quality_row, _normalize_supplier_article,
    _positive_int_set, _cost_coverage_diagnostics, build_data_quality_overview,
    _article_margin_signal, _decision_center_recommendation,
    build_article_margin_view, procurement_recommendations,
    build_consolidated_purchase_plan, render_section_header,
)


def render(ctx: dict) -> None:
    data = ctx['data']

    render_section_header(
        "Продвижение",
        "Сколько заказов принесла реклама, сколько на неё потрачено, доля рекламных расходов "
        "(ДРР) и средний CTR за выбранный период.",
    )
    ad_cols = st.columns(4)
    with ad_cols[0]:
        kpi_card("Сумма заказов", money(data.kpi["ad_revenue"]), "Атрибуция рекламного API")
    with ad_cols[1]:
        kpi_card("Затраты", money(data.kpi["ad_spend"]), "За выбранный период")
    with ad_cols[2]:
        kpi_card("Доля затрат", pct(data.kpi["ad_drr"]), "Затраты / сумма заказов")
    with ad_cols[3]:
        kpi_card("Средний CTR", pct(data.kpi["ad_ctr"]), f"Клики {num(data.kpi['ad_clicks'])}")

    st.caption("Общие показатели считаются по итоговой строке каждой кампании за день — без суммирования дублей по приложениям и товарам.")
    render_section_header(
        "Рекламные кампании",
        "Расход, рекламная выручка, CTR, цена клика (CPC) и ДРР по каждой кампании за период.",
    )
    if data.ads.empty:
        render_empty_state(
            "Рекламных кампаний пока нет",
            "Статистика появится после первой синхронизации с включённой категорией «Продвижение» в токене WB, либо когда запустите рекламу в кабинете WB.",
        )
    else:
        st.dataframe(
            data.ads,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Расход": st.column_config.NumberColumn(format="%.0f ₽"),
                "Рекламная выручка": st.column_config.NumberColumn(format="%.0f ₽"),
                "CTR, %": st.column_config.NumberColumn(format="%.2f%%"),
                "CPC": st.column_config.NumberColumn(format="%.2f ₽"),
                "ДРР, %": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )

