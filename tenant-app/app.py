from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from backup_tools import ensure_daily_backup, latest_backup
from calculations import build_dashboard
from config import BILLING_URL, IS_DEMO, LOGOUT_URL, get_ozon_credentials, get_token, load_settings, save_settings
from db import init_db, last_sync, refresh_auto_costs, table_count
from onboarding import render_page_hint
from ui_helpers import render_setup_checklist

from pages import (
    control, today, overview, finance, production as production_page,
    procurement as procurement_page, products, ads, stock, settings_page,
    agents_page, reports_page,
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
    "Агенты": agents_page,
    "Отчёты": reports_page,
    "Настройки": settings_page,
}
FULL_NAV = ["Сегодня", "Обзор", "Финансы", "Производство", "Закупки", "Товары", "Реклама", "Остатки", "Контроль", "Агенты", "Отчёты", "Настройки"]
# Novice keeps only the headline pages plus Настройки (needed to connect the
# WB token in the first place) -- everything production/procurement/ads-
# related is expert-only complexity a first-time seller doesn't need yet.
NOVICE_NAV = ["Сегодня", "Обзор", "Финансы", "Настройки"]

st.set_page_config(page_title="MARKETSHELPER", page_icon="📊", layout="wide")
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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  /* Same design system as the marketing site (gateway/templates/base.html)
     -- a dark instrument-panel palette with one accent reserved for
     interactive elements, and a separate semantic set (good/warn/critical)
     so "brand" and "needs attention" never look the same. Base colors also
     live in .streamlit/config.toml's [theme] block so Streamlit's own
     native widgets (buttons, inputs, tabs, dataframe header) pick them up
     without fragile CSS targeting; this block layers fonts and the
     custom KPI-card / status-pill / warning-box components on top. */
  --bg:#0a0b12; --surface:#12141e; --surface-2:#1a1d2c;
  --border:rgba(255,255,255,.09); --border-strong:rgba(255,255,255,.17);
  --text:#eef0f7; --text-muted:#8d94aa; --text-faint:#5b6178;
  --accent:#7c6cf6; --accent-strong:#9a8bff; --accent-soft:rgba(124,108,246,.14);
  --good:#3ecf8e; --good-soft:rgba(62,207,142,.12);
  --warn:#f2b84b; --warn-soft:rgba(242,184,75,.12);
  --critical:#f2677a; --critical-soft:rgba(242,103,122,.12);
  --font-display:"IBM Plex Sans",-apple-system,"Segoe UI",Roboto,sans-serif;
  --font-mono:"IBM Plex Mono","SF Mono",monospace;
}
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
  font-family: var(--font-display);
}
[data-testid="stAppViewContainer"] {background: radial-gradient(circle at 20% 0%, var(--accent-soft) 0%, var(--bg) 36%, var(--bg) 100%);}
[data-testid="stSidebar"] {background: var(--surface); border-right: 1px solid var(--border);}
.block-container {padding-top: 1.5rem; padding-bottom: 3rem; max-width: 1600px;}
.sidebar-brand {font-family: var(--font-display); color: var(--text); font-weight: 700; font-size: 1.15rem; letter-spacing: -.01em; margin: 4px 0 14px;}
.wb-title {font-family: var(--font-display); color: var(--text); font-size: 2rem; font-weight: 700; letter-spacing: -.03em; margin-bottom: .15rem;}
.wb-subtitle {color: var(--text-muted); margin-bottom: 1.2rem;}
.kpi-card {background: linear-gradient(180deg, var(--surface-2), var(--surface)); border: 1px solid var(--border); border-radius: 16px; padding: 18px 18px 15px; min-height: 118px; box-shadow: 0 12px 32px rgba(0,0,0,.28);}
.kpi-label {color: var(--text-faint); font-size:.78rem; margin-bottom:.45rem;}
.kpi-value {font-family: var(--font-mono); font-variant-numeric: tabular-nums; color: var(--text); font-size:1.6rem; line-height:1.1; font-weight:600; letter-spacing:-.01em;}
.kpi-note {color: var(--text-faint); font-size:.76rem; margin-top:.5rem;}
.status-pill {display:inline-block; border-radius:999px; padding:.32rem .7rem; background: var(--good-soft); color: var(--good); font-size:.78rem; border:1px solid rgba(62,207,142,.25);}
.status-pill.warn {background: var(--warn-soft); color: var(--warn); border-color: rgba(242,184,75,.3);}
.status-pill.critical {background: var(--critical-soft); color: var(--critical); border-color: rgba(242,103,122,.3);}
.warning-box {background: var(--warn-soft); border:1px solid rgba(242,184,75,.3); border-radius:14px; padding:13px 15px; color: var(--warn); font-size:.9rem;}
.kpi-card-hero {min-height:158px; padding:22px 22px 18px;}
.kpi-card-hero .kpi-label {font-size:.86rem;}
.kpi-card-hero .kpi-value {font-size:2.4rem;}
.problem-row {border:1px solid var(--border); border-radius:12px; padding:10px 14px; margin-bottom:8px; background: var(--surface);}
.problem-note {color: var(--text-muted); font-size:.82rem; margin-top:.3rem;}
.kpi-delta {font-size:.72rem; font-weight:600; white-space:nowrap; vertical-align:middle;}
.kpi-delta.good {color: var(--good);}
.kpi-delta.critical {color: var(--critical);}
.kpi-delta.neutral {color: var(--text-faint);}
.funnel-row {display:flex; align-items:center; gap:12px; margin-bottom:10px;}
.funnel-label {width:120px; flex-shrink:0; color: var(--text-muted); font-size:.82rem;}
.funnel-track {flex:1; background: var(--surface); border:1px solid var(--border); border-radius:999px; height:22px; overflow:hidden;}
.funnel-fill {height:100%; border-radius:999px;}
.funnel-value {width:76px; text-align:right; font-family: var(--font-mono); font-variant-numeric:tabular-nums; color: var(--text); font-size:.85rem;}
.checklist {max-width:520px; margin-top:8px;}
.checklist-row {display:flex; align-items:flex-start; gap:12px; padding:12px 0; border-bottom:1px solid var(--border);}
.checklist-row:last-child {border-bottom:none;}
.checklist-icon {flex-shrink:0; width:22px; height:22px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:.8rem; font-weight:700; margin-top:1px;}
.checklist-icon.good {background: var(--good-soft); color: var(--good);}
.checklist-icon.neutral {background: var(--surface); color: var(--text-faint); border:1px solid var(--border-strong);}
.checklist-label {color: var(--text); font-weight:600; font-size:.92rem;}
.checklist-note {color: var(--text-faint); font-size:.8rem; margin-top:2px;}
.empty-state {text-align:center; padding:48px 20px; color: var(--text-muted);}
.empty-state-icon {font-size:1.8rem; color: var(--text-faint); margin-bottom:10px;}
.empty-state-title {font-weight:600; color: var(--text); font-size:1.02rem; margin-bottom:6px;}
.empty-state-note {font-size:.86rem; color: var(--text-faint); max-width:420px; margin:0 auto;}
.hint-card-title {font-weight:600; color: var(--text); font-size:.94rem; margin-bottom:4px;}
.hint-card-body {color: var(--text-muted); font-size:.86rem; margin-bottom:10px;}
div[data-testid="stDataFrame"] {border:1px solid var(--border); border-radius:14px; overflow:hidden;}
div[data-testid="stMetric"] {background: var(--surface); border: 1px solid var(--border); border-radius: 14px; padding: 12px 16px;}
div[data-testid="stMetricValue"] {font-family: var(--font-mono); font-variant-numeric: tabular-nums;}
.stButton > button {border-radius:12px; font-weight:600; font-family: var(--font-display);}
[data-testid="stAlert"] {border-radius: 12px; font-family: var(--font-display);}
</style>
""",
    unsafe_allow_html=True,
)



settings = load_settings()

with st.sidebar:
    st.markdown('<div class="sidebar-brand">MARKETSHELPER</div>', unsafe_allow_html=True)
    mode_choice = st.segmented_control(
        "Режим",
        ["Новичок", "Опытный"],
        default=("Новичок" if settings.get("ui_mode") == "novice" else "Опытный"),
        key="ui_mode_toggle",
    )
    new_ui_mode = "novice" if mode_choice == "Новичок" else "expert"
    if new_ui_mode != settings.get("ui_mode"):
        settings["ui_mode"] = new_ui_mode
        save_settings(settings)
    if IS_DEMO:
        # Publicly reachable (no login, no ForwardAuth) -- keep it to the
        # read-only headline pages only. No Настройки (token/cost editing),
        # no Товары/Производство/Закупки/etc.
        nav_options = ["Сегодня", "Обзор", "Финансы"]
    else:
        nav_options = NOVICE_NAV if new_ui_mode == "novice" else FULL_NAV
    page = st.radio("", nav_options, label_visibility="collapsed")
    st.divider()
    token_exists = bool(get_token())
    sync_info = None
    backup_info = None
    if IS_DEMO:
        # A real seller's sidebar shows API-connection/sync/backup status --
        # none of that applies to fake demo data, and an "API не подключён"
        # warning here would just look like something is broken.
        st.markdown('<span class="status-pill">● Демо-данные</span>', unsafe_allow_html=True)
    else:
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
    if LOGOUT_URL:
        # /logout on the gateway is POST-only (it revokes the session, not
        # just a navigation) -- st.link_button only ever does a GET, so this
        # needs a real HTML form instead. The session cookie is scoped to
        # the parent domain (see gateway/config.py cookie_domain()), so a
        # plain cross-origin form POST from this subdomain still carries it.
        st.markdown(
            f'''<form action="{LOGOUT_URL}" method="post" style="margin-top: 8px;">
                <button type="submit" style="width:100%; padding:0.5rem 1rem; border-radius:12px;
                    font-weight:600; font-family: var(--font-display); cursor:pointer;
                    background: var(--surface); color: var(--text); border: 1px solid var(--border);">
                    Выйти
                </button>
            </form>''',
            unsafe_allow_html=True,
        )

if page not in {"Настройки", "Контроль", "Агенты", "Отчёты"}:
    st.markdown('<div class="wb-title">Панель управления Wildberries</div>', unsafe_allow_html=True)
    st.markdown('<div class="wb-subtitle">Продажи, финансы, производство, реклама и остатки</div>', unsafe_allow_html=True)
    if IS_DEMO:
        st.info("Это демонстрационный кабинет с вымышленными товарами и цифрами — чтобы показать, как выглядит настоящий рабочий дашборд.")

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
        st.markdown("### Первые шаги")
        render_setup_checklist(token_exists, sync_info)
        st.stop()

    data = build_dashboard(start, end)
else:
    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date()
    period = start = end = data = None

ctx = {
    "settings": settings,
    "token_exists": token_exists,
    "ozon_connected": bool(get_ozon_credentials()),
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
    render_page_hint(page, settings)
    page_module.render(ctx)
