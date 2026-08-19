from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from backup_tools import ensure_daily_backup, latest_backup
from calculations import build_dashboard
from config import BILLING_URL, get_token, load_settings
from db import init_db, last_sync, refresh_auto_costs, table_count

from pages import (
    control, today, overview, finance, production as production_page,
    procurement as procurement_page, products, ads, stock, settings_page,
)

PAGES = {
    "Контроль": control,
    "Сегодня": today,
    "Обзор": overview,
    "Финансы": finance,
    "Производство": production_page,
    "Закупки": procurement_page,
    "Товары": products,
    "Реклама": ads,
    "Остатки": stock,
    "Настройки": settings_page,
}

st.set_page_config(page_title="WB Control", page_icon="📊", layout="wide")
init_db()
try:
    refresh_auto_costs()
except Exception:
    # Missing procurement prices must not block the dashboard.
    pass
try:
    ensure_daily_backup()
except Exception:
    # A backup failure must never prevent the dashboard from opening.
    pass

st.markdown(
    """
<style>
[data-testid="stAppViewContainer"] {background: radial-gradient(circle at 20% 0%, #17152b 0%, #0b0e14 36%, #090b10 100%);}
[data-testid="stSidebar"] {background: #0d1119; border-right: 1px solid rgba(255,255,255,.07);}
.block-container {padding-top: 1.5rem; padding-bottom: 3rem; max-width: 1600px;}
.wb-title {font-size: 2rem; font-weight: 800; letter-spacing: -.04em; margin-bottom: .15rem;}
.wb-subtitle {color: #98a2b3; margin-bottom: 1.2rem;}
.kpi-card {background: linear-gradient(145deg, rgba(28,34,48,.96), rgba(17,22,32,.96)); border: 1px solid rgba(255,255,255,.075); border-radius: 18px; padding: 18px 18px 15px; min-height: 118px; box-shadow: 0 12px 32px rgba(0,0,0,.18);}
.kpi-label {color:#98a2b3; font-size:.84rem; margin-bottom:.45rem;}
.kpi-value {font-size:1.72rem; line-height:1.1; font-weight:800; letter-spacing:-.03em;}
.kpi-note {color:#6f7a8c; font-size:.76rem; margin-top:.5rem;}
.status-pill {display:inline-block; border-radius:999px; padding:.32rem .7rem; background:rgba(87,211,145,.12); color:#79e2aa; font-size:.78rem; border:1px solid rgba(87,211,145,.2);}
.warning-box {background:rgba(247,183,49,.08); border:1px solid rgba(247,183,49,.25); border-radius:14px; padding:13px 15px; color:#f5d37c;}
div[data-testid="stDataFrame"] {border:1px solid rgba(255,255,255,.06); border-radius:14px; overflow:hidden;}
.stButton > button {border-radius:12px; font-weight:650;}
</style>
""",
    unsafe_allow_html=True,
)



settings = load_settings()

with st.sidebar:
    st.markdown("## WB Control")
    page = st.radio("", ["Сегодня", "Обзор", "Финансы", "Производство", "Закупки", "Товары", "Реклама", "Остатки", "Контроль", "Настройки"], label_visibility="collapsed")
    st.divider()
    token_exists = bool(get_token())
    if token_exists:
        st.markdown('<span class="status-pill">● API подключён</span>', unsafe_allow_html=True)
    else:
        st.warning("API пока не подключён")
    sync_info = last_sync()
    if sync_info:
        st.caption(f"Последняя синхронизация: {sync_info.get('finished_at') or sync_info.get('started_at')}")
    backup_info = latest_backup()
    if backup_info:
        st.caption(f"Резервная копия: {backup_info['modified_at']:%d.%m.%Y %H:%M}")
    if BILLING_URL:
        st.divider()
        st.link_button("💳 Подписка", BILLING_URL, use_container_width=True)

if page not in {"Настройки", "Контроль"}:
    st.markdown('<div class="wb-title">Панель управления Wildberries</div>', unsafe_allow_html=True)
    st.markdown('<div class="wb-subtitle">Продажи, финансы, производство, реклама и остатки</div>', unsafe_allow_html=True)

    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    if page == "Сегодня":
        period = "Сегодня"
        start = end = today_msk
        st.caption(f"Оперативная сводка на {today_msk:%d.%m.%Y} (московское время)")
    else:
        period = st.segmented_control(
            "Период",
            ["Сегодня", "7 дней", "30 дней", "90 дней", "Свои даты"],
            default="30 дней",
        )
        days_map = {"Сегодня": 1, "7 дней": 7, "30 дней": 30, "90 дней": 90}
        if period == "Свои даты":
            picked = st.date_input(
                "Точный период",
                value=(today_msk - timedelta(days=29), today_msk),
                format="DD.MM.YYYY",
            )
            if isinstance(picked, (tuple, list)) and len(picked) == 2:
                start, end = picked
            else:
                start = end = picked if not isinstance(picked, (tuple, list)) else today_msk
        else:
            days = days_map.get(period, 30)
            end = today_msk
            start = end - timedelta(days=days - 1)
        if start > end:
            start, end = end, start
        st.caption(f"Период данных: {start:%d.%m.%Y}–{end:%d.%m.%Y} (московское время)")

    if table_count("orders") == 0 and table_count("sales") == 0:
        st.info("Данных пока нет. Откройте «Настройки», сохраните токен и нажмите «Синхронизировать», либо загрузите демонстрационные данные.")
        st.stop()

    data = build_dashboard(start, end)
else:
    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    period = start = end = data = None

ctx = {
    "settings": settings,
    "token_exists": token_exists,
    "sync_info": sync_info,
    "backup_info": backup_info,
    "period": period,
    "start": start,
    "end": end,
    "data": data,
    "today_msk": today_msk,
}

page_module = PAGES.get(page)
if page_module is not None:
    page_module.render(ctx)
