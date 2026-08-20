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

    st.markdown("### Экономика по артикулам")
    if data.products.empty:
        st.info("Нет данных по товарам за выбранный период.")
    else:
        st.dataframe(
            data.products,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Выручка": st.column_config.NumberColumn(format="%.0f ₽"),
                "Выкуп, %": st.column_config.NumberColumn(format="%.1f%%"),
                "Реклама": st.column_config.NumberColumn(format="%.0f ₽"),
                "ДРР, %": st.column_config.NumberColumn(format="%.1f%%"),
                "Продаж/день": st.column_config.NumberColumn(format="%.2f"),
                "Запас, дней": st.column_config.NumberColumn(format="%.1f"),
                "Себестоимость ед.": st.column_config.NumberColumn(format="%.0f ₽"),
                "Маржа до комиссий WB": st.column_config.NumberColumn(format="%.0f ₽"),
            },
        )

