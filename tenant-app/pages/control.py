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
    money, num, pct, PRODUCTION_RULES, production_rule, apply_production_rules,
    infer_material_name, material_key, ceil_to_batch, kpi_card,
    _parse_local_datetime, _quality_row, _normalize_supplier_article,
    _positive_int_set, _cost_coverage_diagnostics, build_data_quality_overview,
    _article_margin_signal, _decision_center_recommendation,
    build_article_margin_view, procurement_recommendations,
    build_consolidated_purchase_plan,
)
from datetime import (
    datetime,
)
from db import (
    finished_goods_fifo_guard_status,
    process_sales_fifo_events,
)
from sync import (
    sync_all,
)
from zoneinfo import (
    ZoneInfo,
)


def render(ctx: dict) -> None:
    backup_info = ctx['backup_info']
    settings = ctx['settings']
    sync_info = ctx['sync_info']
    token_exists = ctx['token_exists']

    st.markdown('<div class="wb-title">Контроль качества данных</div>', unsafe_allow_html=True)
    st.markdown('<div class="wb-subtitle">Показывает, каким расчётам можно доверять сейчас и какие данные действительно блокируют решения.</div>', unsafe_allow_html=True)
    quality_df, quality_summary, reliability_df, cost_quality_df = build_data_quality_overview(settings, token_exists, sync_info, backup_info)
    fifo_guard = finished_goods_fifo_guard_status(
        str((sync_info or {}).get("finished_at") or (sync_info or {}).get("started_at") or "")
    )
    control_cols = st.columns(6)
    with control_cols[0]:
        kpi_card("Готовность данных", f"{float(quality_summary['score']):.0f}%", "Обязательные проверки")
    with control_cols[1]:
        kpi_card("Критично", num(int(quality_summary["critical"])), "Блокирует часть расчётов")
    with control_cols[2]:
        kpi_card("Ожидание API", num(int(quality_summary.get("waiting", 0))), "Не снижает готовность")
    with control_cols[3]:
        kpi_card("Внимание", num(int(quality_summary["warnings"])), "Можно работать с оговорками")
    with control_cols[4]:
        kpi_card("Готово", num(int(quality_summary["ready"])), "Включая ожидание API")
    with control_cols[5]:
        kpi_card("Отложено", num(int(quality_summary["optional"])), "Не входит в общий балл")

    if int(quality_summary["critical"]) == 0:
        if int(quality_summary.get("waiting", 0)) > 0:
            st.success("Критических проблем нет. Небольшое расхождение FIFO ожидает выравнивания API WB и не снижает готовность расчётов.")
        else:
            st.success("Критических проблем нет. Основные расчёты можно использовать; предупреждения указаны ниже.")
    else:
        st.warning("Есть устойчивые или крупные критические пробелы. Перед действиями по затронутым модулям выполните рекомендации из таблицы.")
    st.info("Поставщики, закупочные цены и MOQ являются необязательными. Пока вы не используете реальный сводный план закупок, их отсутствие не снижает готовность финансов, производства, остатков и FIFO.")

    if "fifo_full_recheck_message" in st.session_state:
        st.success(st.session_state.pop("fifo_full_recheck_message"))
    recheck_cols = st.columns([1, 2])
    with recheck_cols[0]:
        if st.button("Полная перепроверка FIFO", type="primary", use_container_width=True, key="control_full_fifo_recheck"):
            try:
                with st.spinner("Последовательно синхронизирую WB, обрабатываю продажи и пересчитываю расхождение..."):
                    sync_result = sync_all()
                    fifo_result = process_sales_fifo_events()
                st.session_state["fifo_full_recheck_message"] = (
                    f"Полная перепроверка завершена. Синхронизация: {sync_result}; "
                    f"обработано FIFO-операций: {int(fifo_result.get('processed', 0) or 0)}, "
                    f"ошибок: {int(fifo_result.get('errors', 0) or 0)}."
                )
                st.cache_data.clear(); st.rerun()
            except Exception as exc:
                st.error(str(exc))
    with recheck_cols[1]:
        guard_status = str(fifo_guard.get("status", "Готово") or "Готово")
        if guard_status == "Ожидание API":
            st.info(
                f"FIFO: расхождение {int(fifo_guard.get('total_units', 0) or 0)} ед., "
                f"цикл {int(fifo_guard.get('cycles', 0) or 0)}, возраст {float(fifo_guard.get('age_minutes', 0) or 0):.0f} мин. "
                "Ручная сверка временно не требуется."
            )
        elif guard_status == "Внимание" and str(fifo_guard.get("guard_mode", "") or "") == "wb_incident_review":
            st.warning(str(fifo_guard.get("reason", "WB-расхождение требует отдельной проверки и не должно списываться обычной сверкой.")))
        elif guard_status == "Критично":
            st.warning(str(fifo_guard.get("reason", "Устойчивое локальное расхождение FIFO.")))
        else:
            st.success("FIFO готовой продукции совпадает с физическими остатками.")

    quality_tabs = st.tabs(["Надёжность расчётов", "Все проверки", "Что исправить"])
    with quality_tabs[0]:
        reliability_rank = {"Надёжно": 0, "С ограничениями": 1, "Ненадёжно": 2, "Не настроено": 3, "Отложено": 4}
        reliability_view = reliability_df.copy()
        reliability_view["_rank"] = reliability_view["Надёжность"].map(reliability_rank).fillna(9)
        reliability_view = reliability_view.sort_values("_rank").drop(columns="_rank")
        st.dataframe(reliability_view, hide_index=True, use_container_width=True, height=260)
        st.caption("Статус относится к текущим данным на этом компьютере. Он не является гарантией результата и не заменяет сверку с кабинетом WB.")
    with quality_tabs[1]:
        status_order = {"Критично": 0, "Внимание": 1, "Ожидание API": 2, "Готово": 3, "Отложено": 4}
        all_checks = quality_df.copy()
        all_checks["_rank"] = all_checks["Статус"].map(status_order).fillna(9)
        all_checks = all_checks.sort_values(["_rank", "Модуль", "Проверка"]).drop(columns=["_rank", "Вес", "Баллы", "Обязательная"])
        st.dataframe(all_checks, hide_index=True, use_container_width=True, height=min(700, 110 + 38 * len(all_checks)))
        st.download_button(
            "Скачать отчёт качества данных CSV",
            all_checks.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"wb_data_quality_{datetime.now(ZoneInfo('Europe/Moscow')):%Y%m%d_%H%M}.csv",
            mime="text/csv",
            key="download_data_quality_csv",
        )
        with st.expander("Диагностика сопоставления себестоимости активных товаров", expanded=False):
            if cost_quality_df.empty:
                st.info("Активные товары для проверки не определены.")
            else:
                cost_issue_count = int(cost_quality_df["Статус"].ne("Покрыт").sum())
                if cost_issue_count == 0:
                    st.success("Все активные товары сопоставлены с положительной себестоимостью.")
                else:
                    st.warning(f"Найдены проблемные позиции: {cost_issue_count}. Они показаны первыми в таблице.")
                st.dataframe(
                    cost_quality_df, hide_index=True, use_container_width=True,
                    height=min(700, 110 + 38 * len(cost_quality_df)),
                    column_config={"Ставка, ₽": st.column_config.NumberColumn(format="%.2f")},
                )
                st.download_button(
                    "Скачать диагностику себестоимости CSV",
                    cost_quality_df.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"wb_cost_quality_{datetime.now(ZoneInfo('Europe/Moscow')):%Y%m%d_%H%M}.csv",
                    mime="text/csv", key="download_cost_quality_csv",
                )
    with quality_tabs[2]:
        issues = quality_df[quality_df["Статус"].isin(["Критично", "Внимание"])].copy()
        if issues.empty:
            st.success("Обязательных действий по качеству данных сейчас нет.")
        else:
            issues["Приоритет"] = issues["Статус"].map({"Критично": "1 — сейчас", "Внимание": "2 — затем"})
            st.dataframe(
                issues[["Приоритет", "Модуль", "Проверка", "Детали", "Что сделать"]].sort_values("Приоритет"),
                hide_index=True, use_container_width=True, height=min(600, 110 + 46 * len(issues)),
            )
        cost_issues = cost_quality_df[cost_quality_df["Статус"].ne("Покрыт")].copy() if not cost_quality_df.empty else pd.DataFrame()
        if not cost_issues.empty:
            st.markdown("#### Конкретные позиции без подтверждённой ставки")
            st.dataframe(
                cost_issues[["Артикул WB", "Артикул продавца", "Товар", "Ставка, ₽", "Сопоставление", "Статус"]],
                hide_index=True, use_container_width=True,
                column_config={"Ставка, ₽": st.column_config.NumberColumn(format="%.2f")},
            )
            st.caption("«Нет строки» и «Нулевая ставка» разделены. Сопоставление сначала выполняется по артикулу WB, затем — по нормализованному артикулу продавца.")
        optional_rows = quality_df[quality_df["Статус"].eq("Отложено")]
        if not optional_rows.empty:
            st.markdown("#### Необязательные настройки")
            st.dataframe(optional_rows[["Модуль", "Проверка", "Детали", "Что сделать"]], hide_index=True, use_container_width=True)

