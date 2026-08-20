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
    _parse_local_datetime, _quality_row, _normalize_supplier_article,
    _positive_int_set, _cost_coverage_diagnostics, build_data_quality_overview,
    _article_margin_signal, _decision_center_recommendation,
    build_article_margin_view, procurement_recommendations,
    build_consolidated_purchase_plan,
)


def render(ctx: dict) -> None:
    data = ctx['data']

    st.markdown("### Продвижение")
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
    st.markdown("### Рекламные кампании")
    if data.ads.empty:
        st.info("Рекламная статистика отсутствует или категория «Продвижение» не включена в токене.")
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

