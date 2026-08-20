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
from config import (
    get_token,
)
from db import (
    apply_finished_goods_reconciliation,
    apply_retroactive_fifo_reconstruction_v67,
    apply_wb_incident_loss,
    create_wb_incident_case,
    delete_wb_incident_compensation,
    fifo_reconciliation_status,
    finished_goods_fifo_guard_status,
    last_sync,
    preview_finished_goods_reconciliation,
    preview_retroactive_fbw_fifo_reconstruction,
    process_sales_fifo_events,
    read_fifo_reconciliation_lines,
    read_fifo_reconciliation_runs,
    read_fifo_reconstruction_apply_runs,
    read_fifo_reconstruction_v67_readiness,
    read_finished_goods_cost_layers,
    read_finished_goods_fifo_allocations,
    read_finished_goods_fifo_summary,
    read_inventory_valuation,
    read_sales_fifo_allocations,
    read_sales_fifo_events,
    read_verified_fbw_supplies,
    read_verified_fbw_supply_goods,
    read_wb_final_evidence_audit,
    read_wb_incident_cases,
    read_wb_incident_compensations,
    read_wb_incident_exposure,
    read_wb_incident_loss_lines,
    read_wb_incident_reconciliation,
    read_wb_income_supply_evidence,
    record_wb_incident_compensation,
    reverse_wb_incident_loss,
    rollback_retroactive_fifo_reconstruction_v67,
    sales_fifo_tracking_status,
    save_verified_fbw_supply,
    update_wb_incident_case,
    wb_fifo_reconciliation_context,
    wb_incident_financial_summary,
)
from sync import (
    sync_all,
)
from wb_api import (
    WBAPI,
)


def render(ctx: dict) -> None:
    data = ctx['data']
    today_msk = ctx['today_msk']

    stock_tabs = st.tabs(["По складам WB", "Стоимостная оценка", "FIFO готовой продукции", "FIFO продаж", "Сверка FIFO", "Инциденты WB"])
    with stock_tabs[0]:
        st.markdown("### Остатки по складам")
        if data.stocks.empty:
            st.info("Остатки отсутствуют или категория «Аналитика» не включена в токене.")
        else:
            st.dataframe(data.stocks, hide_index=True, use_container_width=True)

    with stock_tabs[1]:
        st.markdown("### Стоимость товарного запаса")
        valuation = read_inventory_valuation()
        if valuation.empty:
            st.info("Нет товаров для стоимостной оценки.")
        else:
            numeric_cols = [
                "wb_units", "ready_units", "inbound_units", "baseline_unit_cost", "actual_batch_unit_cost",
                "valuation_unit_cost", "wb_value_rub", "ready_value_rub", "inbound_value_rub", "actual_profile_units",
                "wb_fifo_unit_cost", "ready_fifo_unit_cost", "inbound_fifo_unit_cost",
                "wb_fifo_coverage_pct", "ready_fifo_coverage_pct", "inbound_fifo_coverage_pct",
                "wb_layer_units", "ready_layer_units", "inbound_layer_units", "active_finished_layers"
            ]
            for col in numeric_cols:
                valuation[col] = pd.to_numeric(valuation.get(col, 0), errors="coerce").fillna(0.0)
            total_wb = float(valuation["wb_value_rub"].sum())
            total_ready = float(valuation["ready_value_rub"].sum())
            total_inbound = float(valuation["inbound_value_rub"].sum())
            total_value = total_wb + total_ready + total_inbound
            actual_articles = int((valuation["actual_batch_unit_cost"] > 0).sum())
            fifo_articles = int(((valuation["wb_layer_units"] + valuation["ready_layer_units"] + valuation["inbound_layer_units"]) > 0).sum())
            known_ready = int(pd.to_numeric(valuation.get("ready_known", 0), errors="coerce").fillna(0).sum())
            known_inbound = int(pd.to_numeric(valuation.get("inbound_known", 0), errors="coerce").fillna(0).sum())
            cards = st.columns(5)
            with cards[0]: kpi_card("Общая стоимость", money(total_value), "WB + готово + в пути")
            with cards[1]: kpi_card("На складах WB", money(total_wb), num(float(valuation["wb_units"].sum())) + " компл./шт.")
            with cards[2]: kpi_card("Готово у вас", money(total_ready), num(float(valuation["ready_units"].sum())) + " компл.")
            with cards[3]: kpi_card("В пути", money(total_inbound), num(float(valuation["inbound_units"].sum())) + " компл.")
            with cards[4]: kpi_card("FIFO-профили", num(fifo_articles), f"Закрытые партии: {actual_articles}")
            if known_ready < len(valuation) or known_inbound < len(valuation):
                st.info("Неподтверждённые остатки готовой продукции и поставок считаются нулевыми только в этой оценке. Подтвердить их можно в настройках готовой продукции.")
            view = valuation.rename(columns={
                "nm_id": "Артикул WB", "supplier_article": "Артикул продавца", "product_name": "Товар",
                "source_type": "Источник", "wb_units": "На WB, шт", "ready_units": "Готово, шт",
                "inbound_units": "В пути, шт", "baseline_unit_cost": "Базовая ставка, ₽",
                "actual_batch_unit_cost": "Факт. партия, ₽", "valuation_unit_cost": "Ставка оценки, ₽",
                "wb_fifo_unit_cost": "FIFO на WB, ₽", "ready_fifo_unit_cost": "FIFO готового, ₽",
                "inbound_fifo_unit_cost": "FIFO в пути, ₽",
                "wb_fifo_coverage_pct": "Покрытие WB, %", "ready_fifo_coverage_pct": "Покрытие готового, %",
                "inbound_fifo_coverage_pct": "Покрытие в пути, %",
                "cost_source": "Источник ставки", "actual_profile_units": "Произведено по факту, шт",
                "latest_batch_date": "Последняя партия", "wb_value_rub": "Стоимость WB, ₽",
                "ready_value_rub": "Стоимость готового, ₽", "inbound_value_rub": "Стоимость в пути, ₽",
            })
            view["Всего единиц"] = view["На WB, шт"] + view["Готово, шт"] + view["В пути, шт"]
            view["Общая стоимость, ₽"] = view["Стоимость WB, ₽"] + view["Стоимость готового, ₽"] + view["Стоимость в пути, ₽"]
            display_cols = [
                "Артикул продавца", "Товар", "Источник", "На WB, шт", "Готово, шт", "В пути, шт",
                "Всего единиц", "Базовая ставка, ₽", "Факт. партия, ₽",
                "FIFO на WB, ₽", "FIFO готового, ₽", "FIFO в пути, ₽",
                "Покрытие WB, %", "Покрытие готового, %", "Покрытие в пути, %",
                "Ставка оценки, ₽", "Источник ставки", "Общая стоимость, ₽", "Последняя партия"
            ]
            st.dataframe(
                view[display_cols], hide_index=True, use_container_width=True, height=560,
                column_config={
                    "На WB, шт": st.column_config.NumberColumn(format="%.0f"),
                    "Готово, шт": st.column_config.NumberColumn(format="%.0f"),
                    "В пути, шт": st.column_config.NumberColumn(format="%.0f"),
                    "Всего единиц": st.column_config.NumberColumn(format="%.0f"),
                    "Базовая ставка, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Факт. партия, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "FIFO на WB, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "FIFO готового, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "FIFO в пути, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Покрытие WB, %": st.column_config.NumberColumn(format="%.1f%%"),
                    "Покрытие готового, %": st.column_config.NumberColumn(format="%.1f%%"),
                    "Покрытие в пути, %": st.column_config.NumberColumn(format="%.1f%%"),
                    "Ставка оценки, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Общая стоимость, ₽": st.column_config.NumberColumn(format="%.2f"),
                },
            )
            st.caption("Готовый склад, товар в пути и остаток WB оцениваются по своим FIFO-слоям. Непокрытая слоями часть оценивается по базовой себестоимости. Новые продажи после включения версии 4.6 списываются по конкретным FIFO-слоям; исторические операции остаются на базовой оценке.")

    with stock_tabs[2]:
        st.markdown("### Послойная стоимость готовой продукции")
        st.caption(
            "Слой создаётся при закрытии производственной смены или приёмке закупаемого товара. "
            "При отгрузке стоимость перемещается из готового остатка в путь, после приёмки — на WB."
        )
        fg_summary = read_finished_goods_fifo_summary()
        if fg_summary.empty:
            st.info("FIFO-слои готовой продукции ещё не созданы. Инициализировать их можно в Настройках → 5.2.")
        else:
            summary_numeric = [
                "ready_physical", "ready_layer_units", "ready_rate_rub", "inbound_physical", "inbound_layer_units",
                "inbound_rate_rub", "wb_physical", "wb_layer_units", "wb_rate_rub", "active_layers",
                "ready_difference", "inbound_difference", "wb_difference"
            ]
            for col in summary_numeric:
                fg_summary[col] = pd.to_numeric(fg_summary.get(col, 0), errors="coerce").fillna(0)
            mismatch = int((
                fg_summary[["ready_difference", "inbound_difference", "wb_difference"]].abs().max(axis=1) > 0
            ).sum())
            cards = st.columns(4)
            with cards[0]: kpi_card("Слоёв", num(float(fg_summary["active_layers"].sum())), "Активные партии")
            with cards[1]: kpi_card("Готово в слоях", num(float(fg_summary["ready_layer_units"].sum())), "Комплектов / единиц")
            with cards[2]: kpi_card("В пути в слоях", num(float(fg_summary["inbound_layer_units"].sum())), "Комплектов / единиц")
            with cards[3]: kpi_card("Требует сверки", num(mismatch), "Артикулов")
            st.dataframe(
                fg_summary[[
                    "supplier_article", "product_name", "ready_physical", "ready_layer_units", "ready_rate_rub",
                    "inbound_physical", "inbound_layer_units", "inbound_rate_rub", "wb_physical", "wb_layer_units",
                    "wb_rate_rub", "active_layers", "ready_difference", "inbound_difference", "wb_difference"
                ]],
                hide_index=True, use_container_width=True, height=520,
                column_config={
                    "supplier_article": "Артикул продавца", "product_name": "Товар",
                    "ready_physical": st.column_config.NumberColumn("Готово физически", format="%d"),
                    "ready_layer_units": st.column_config.NumberColumn("Готово в слоях", format="%d"),
                    "ready_rate_rub": st.column_config.NumberColumn("Ставка готового, ₽", format="%.2f"),
                    "inbound_physical": st.column_config.NumberColumn("В пути физически", format="%d"),
                    "inbound_layer_units": st.column_config.NumberColumn("В пути в слоях", format="%d"),
                    "inbound_rate_rub": st.column_config.NumberColumn("Ставка в пути, ₽", format="%.2f"),
                    "wb_physical": st.column_config.NumberColumn("На WB физически", format="%d"),
                    "wb_layer_units": st.column_config.NumberColumn("На WB в слоях", format="%d"),
                    "wb_rate_rub": st.column_config.NumberColumn("Ставка WB, ₽", format="%.2f"),
                    "active_layers": st.column_config.NumberColumn("Слоёв", format="%d"),
                    "ready_difference": st.column_config.NumberColumn("Разница готового", format="%d"),
                    "inbound_difference": st.column_config.NumberColumn("Разница в пути", format="%d"),
                    "wb_difference": st.column_config.NumberColumn("Разница WB", format="%d"),
                },
            )
            with st.expander("Показать слои и движения"):
                layer_tab, movement_tab = st.tabs(["Слои", "Движения стоимости"])
                with layer_tab:
                    layers = read_finished_goods_cost_layers(False)
                    if layers.empty:
                        st.info("Слои отсутствуют.")
                    else:
                        layers["source_date"] = pd.to_datetime(layers["source_date"], errors="coerce")
                        st.dataframe(
                            layers[[
                                "id", "source_date", "supplier_article", "product_name", "source_type", "source_ref",
                                "original_units", "ready_units", "inbound_units", "wb_units", "unit_cost_rub",
                                "original_amount_rub", "status", "note"
                            ]], hide_index=True, use_container_width=True, height=420,
                            column_config={
                                "id": st.column_config.NumberColumn("Слой", format="%d"),
                                "source_date": st.column_config.DateColumn("Дата", format="DD.MM.YYYY"),
                                "supplier_article": "Артикул продавца", "product_name": "Товар",
                                "source_type": "Источник", "source_ref": "Основание",
                                "original_units": st.column_config.NumberColumn("Поступило", format="%d"),
                                "ready_units": st.column_config.NumberColumn("Готово", format="%d"),
                                "inbound_units": st.column_config.NumberColumn("В пути", format="%d"),
                                "wb_units": st.column_config.NumberColumn("На WB", format="%d"),
                                "unit_cost_rub": st.column_config.NumberColumn("Себестоимость ед., ₽", format="%.2f"),
                                "original_amount_rub": st.column_config.NumberColumn("Стоимость слоя, ₽", format="%.2f"),
                                "status": "Статус", "note": "Примечание",
                            },
                        )
                with movement_tab:
                    allocations = read_finished_goods_fifo_allocations(500)
                    if allocations.empty:
                        st.info("Движений стоимости ещё нет.")
                    else:
                        st.dataframe(
                            allocations[[
                                "id", "movement_id", "supplier_article", "product_name", "layer_id", "units",
                                "amount_rub", "from_location", "to_location", "unit_cost_rub", "status", "created_at"
                            ]], hide_index=True, use_container_width=True, height=420,
                            column_config={
                                "id": st.column_config.NumberColumn("Запись", format="%d"),
                                "movement_id": st.column_config.NumberColumn("Движение", format="%d"),
                                "supplier_article": "Артикул продавца", "product_name": "Товар",
                                "layer_id": st.column_config.NumberColumn("Слой", format="%d"),
                                "units": st.column_config.NumberColumn("Единиц", format="%d"),
                                "amount_rub": st.column_config.NumberColumn("Стоимость, ₽", format="%.2f"),
                                "from_location": "Откуда", "to_location": "Куда",
                                "unit_cost_rub": st.column_config.NumberColumn("Ставка, ₽", format="%.2f"),
                                "status": "Статус", "created_at": "Проведено",
                            },
                        )

    with stock_tabs[3]:
        st.markdown("### FIFO продаж и возвратов")
        fifo_status = sales_fifo_tracking_status()
        status_cols = st.columns(5)
        with status_cols[0]: kpi_card("Продаж списано", num(fifo_status.get("sales_applied", 0)), "После включения учёта")
        with status_cols[1]: kpi_card("Возвратов восстановлено", num(fifo_status.get("returns_applied", 0)), "По исходным слоям")
        with status_cols[2]: kpi_card("Историческая база", num(fifo_status.get("baseline_rows", 0)), "Без точного слоя")
        with status_cols[3]: kpi_card("Ошибки", num(fifo_status.get("errors", 0)), "Требуют повторной обработки")
        with status_cols[4]: kpi_card("Последняя операция", str(fifo_status.get("last_event_date") or "—")[:10], "Дата API-события")
        recent_fifo_sales = read_sales_fifo_events(500)
        if recent_fifo_sales.empty:
            st.info("Операций FIFO продаж пока нет. Исторические строки будут зарегистрированы как база после инициализации.")
        else:
            recent_fifo_sales["event_date"] = pd.to_datetime(recent_fifo_sales["event_date"], errors="coerce")
            st.dataframe(
                recent_fifo_sales[["id", "event_date", "event_type", "status", "supplier_article", "product_name",
                                   "sale_id", "srid", "fifo_cost_rub", "matched_sale_event_id", "note"]],
                hide_index=True, use_container_width=True, height=460,
                column_config={
                    "id": st.column_config.NumberColumn("Событие", format="%d"),
                    "event_date": st.column_config.DatetimeColumn("Дата", format="DD.MM.YYYY HH:mm"),
                    "event_type": "Тип", "status": "Статус", "supplier_article": "Артикул продавца",
                    "product_name": "Товар", "sale_id": "Sale ID", "srid": "SRID",
                    "fifo_cost_rub": st.column_config.NumberColumn("FIFO-себестоимость, ₽", format="%.2f"),
                    "matched_sale_event_id": st.column_config.NumberColumn("Исходная продажа", format="%d"),
                    "note": "Примечание",
                },
            )
            with st.expander("Показать привязку к слоям"):
                sale_alloc = read_sales_fifo_allocations(500)
                if sale_alloc.empty:
                    st.info("Точных распределений по слоям пока нет.")
                else:
                    st.dataframe(
                        sale_alloc, hide_index=True, use_container_width=True, height=420,
                        column_config={
                            "fifo_cost_rub": st.column_config.NumberColumn(format="%.2f"),
                            "amount_rub": st.column_config.NumberColumn(format="%.2f"),
                            "unit_cost_rub": st.column_config.NumberColumn(format="%.2f"),
                        },
                    )

    with stock_tabs[4]:
        st.markdown("### Сверка FIFO — безопасный режим WB")
        st.caption(
            "v5.9 разделяет обычную локальную сверку и расхождения на стороне WB. "
            "Для WB остаток quantity больше не считается единственным физическим остатком: отдельно учитываются "
            "товары к клиенту и от клиента. WB-расхождения здесь диагностические и автоматически из FIFO не списываются."
        )

        recon_preview = preview_finished_goods_reconciliation()
        recon_status = fifo_reconciliation_status()
        wb_context = wb_fifo_reconciliation_context()
        incident_exposure = read_wb_incident_exposure()
        added_now = int(recon_preview.loc[recon_preview["difference_units"] > 0, "difference_units"].sum()) if not recon_preview.empty else 0
        removed_now = int((-recon_preview.loc[recon_preview["difference_units"] < 0, "difference_units"]).sum()) if not recon_preview.empty else 0
        blocked_now = int(pd.to_numeric(recon_preview.loc[pd.to_numeric(recon_preview.get("safe_to_reconcile", 0), errors="coerce").fillna(0).astype(int).ne(1), "difference_units"], errors="coerce").fillna(0).abs().sum()) if not recon_preview.empty else 0
        value_now = float(recon_preview["amount_rub"].sum()) if not recon_preview.empty else 0.0

        rcards = st.columns(5)
        with rcards[0]: kpi_card("Артикулов", num(recon_preview["nm_id"].nunique() if not recon_preview.empty else 0), "С диагностическим расхождением")
        with rcards[1]: kpi_card("Диагн. +", num(added_now), "Факт/контур выше слоёв")
        with rcards[2]: kpi_card("Диагн. −", num(removed_now), "Слои выше факта/контура")
        with rcards[3]: kpi_card("Заблокировано", num(blocked_now), "WB-единиц нельзя автосписывать")
        with rcards[4]: kpi_card("Оценка стоимости", money(value_now), "Только диагностическая оценка")

        st.markdown("#### Контур WB и возможное внешнее выбытие")
        wb_cards = st.columns(6)
        with wb_cards[0]: kpi_card("Доступно WB", num(int(wb_context.get("available_units", 0) or 0)), "quantity")
        with wb_cards[1]: kpi_card("К клиенту", num(int(wb_context.get("in_way_to_client_units", 0) or 0)), "inWayToClient")
        with wb_cards[2]: kpi_card("От клиента", num(int(wb_context.get("in_way_from_client_units", 0) or 0)), "inWayFromClient")
        with wb_cards[3]: kpi_card("В контуре WB", num(int(wb_context.get("wb_contour_units", 0) or 0)), "Доступно + оба пути")
        with wb_cards[4]: kpi_card("FIFO на WB", num(int(wb_context.get("fifo_wb_units", 0) or 0)), "Стоимостные слои")
        with wb_cards[5]: kpi_card("Внешний разрыв*", num(int(wb_context.get("diagnostic_external_gap_units", 0) or 0)), "Ориентир, не списание")

        last_detailed = str(wb_context.get("last_detailed_snapshot_at", "") or "")[:19]
        if bool(wb_context.get("aggregated_snapshot", False)):
            st.warning(
                "Последний снимок WB агрегирован в единый склад (warehouseId = -999999), поэтому детализация по складам сейчас недоступна. "
                "Автоматическое списание или досоздание WB-слоёв заблокировано. Это защищает FIFO от ошибочного списания товаров, "
                "которые могли оказаться в пути, быть выведены из остатка после складского инцидента или позже попасть в компенсацию WB."
            )
        if last_detailed:
            st.info(
                f"Последний детальный складской снимок: {last_detailed}. На нём в полном контуре WB было "
                f"{int(wb_context.get('last_detailed_contour_units',0) or 0)} ед. После него в API продаж прошло "
                f"{int(wb_context.get('sales_since_detailed',0) or 0)} продаж и {int(wb_context.get('returns_since_detailed',0) or 0)} возвратов. "
                f"Расчётный внешний разрыв {int(wb_context.get('diagnostic_external_gap_units',0) or 0)} ед. — только диагностический ориентир: "
                "он не учитывает возможные новые поставки, ручные перемещения и будущие компенсационные документы."
            )

        if not incident_exposure.empty:
            st.markdown("##### Экспозиция на затронутых складах в последнем детальном снимке")
            incident_view = incident_exposure.rename(columns={
                "warehouse": "Склад / классификация",
                "wb_names": "Название в WB",
                "available_units": "Доступно",
                "in_way_to_client_units": "К клиенту",
                "in_way_from_client_units": "От клиента",
                "contour_units": "Всего в контуре",
            })
            st.dataframe(
                incident_view[["Склад / классификация", "Название в WB", "Доступно", "К клиенту", "От клиента", "Всего в контуре"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "Доступно": st.column_config.NumberColumn(format="%d"),
                    "К клиенту": st.column_config.NumberColumn(format="%d"),
                    "От клиента": st.column_config.NumberColumn(format="%d"),
                    "Всего в контуре": st.column_config.NumberColumn(format="%d"),
                },
            )
            st.caption(
                f"На этих складах было доступно {int(wb_context.get('incident_available_units',0) or 0)} ед.; "
                f"с учётом пути — {int(wb_context.get('incident_contour_units',0) or 0)} ед. "
                "Тула в этом блоке трактуется как Тула / Алексин по классификации WB. Эти количества не признаются утраченными автоматически."
            )

        sales_control = sales_fifo_tracking_status()
        current_sync_state = last_sync() or {}
        fifo_guard = finished_goods_fifo_guard_status(
            str(current_sync_state.get("finished_at") or current_sync_state.get("started_at") or "")
        )
        guard_cols = st.columns(5)
        with guard_cols[0]: kpi_card("Статус", str(fifo_guard.get("status", "Готово")), "Защита FIFO")
        with guard_cols[1]: kpi_card("Циклов", num(int(fifo_guard.get("cycles", 0) or 0)), "Только новые наблюдения")
        with guard_cols[2]: kpi_card("Возраст", f"{float(fifo_guard.get('age_minutes', 0) or 0):.0f} мин.", "С первого обнаружения")
        with guard_cols[3]: kpi_card("Снимок WB", str(fifo_guard.get("stock_snapshot_at", "") or "—")[:19], "Контрольное время")
        with guard_cols[4]: kpi_card("FIFO-операция", str(fifo_guard.get("last_fifo_event_at", "") or "—")[:19], "Последняя обработанная")

        if int(sales_control.get("errors", 0) or 0) > 0:
            st.error("Есть ошибки обработки FIFO-продаж. Сначала откройте Настройки → 5.3 и повторите ошибочные операции.")
        guard_state = str(fifo_guard.get("status", "Готово") or "Готово")
        guard_mode = str(fifo_guard.get("guard_mode", "") or "")
        if guard_state == "Внимание" and guard_mode == "wb_incident_review":
            st.warning(str(fifo_guard.get("reason", "WB-расхождение требует отдельной проверки.")))
        elif guard_state == "Ожидание API":
            st.info(str(fifo_guard.get("reason", "Данные WB ещё могут догонять FIFO.")))
        elif guard_state == "Критично":
            st.warning(str(fifo_guard.get("reason", "Локальное расхождение признано устойчивым или крупным.")))

        if not recon_preview.empty:
            if st.button("Полная перепроверка: синхронизация → продажи → FIFO", use_container_width=True, key="stock_full_fifo_recheck"):
                try:
                    with st.spinner("Синхронизирую WB и обрабатываю новые продажи/возвраты..."):
                        sync_result = sync_all()
                        fifo_result = process_sales_fifo_events()
                    st.session_state["fifo_stock_recheck_message"] = (
                        f"Перепроверка завершена. Синхронизация: {sync_result}; "
                        f"обработано FIFO-операций: {int(fifo_result.get('processed', 0) or 0)}, "
                        f"ошибок: {int(fifo_result.get('errors', 0) or 0)}."
                    )
                    st.cache_data.clear(); st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        if "fifo_stock_recheck_message" in st.session_state:
            st.success(st.session_state.pop("fifo_stock_recheck_message"))

        if recon_preview.empty:
            st.success("Текущих диагностических расхождений нет.")
        else:
            view = recon_preview.rename(columns={
                "supplier_article":"Артикул продавца", "product_name":"Товар", "location_name":"Место",
                "physical_units":"Факт / контур", "layered_units":"В слоях", "difference_units":"Разница",
                "action":"Действие", "unit_cost_rub":"Ставка, ₽", "amount_rub":"Стоимость, ₽",
                "risk_status":"Статус", "physical_basis":"Основа сравнения",
                "wb_available_units":"Доступно WB", "wb_in_way_to_client_units":"К клиенту",
                "wb_in_way_from_client_units":"От клиента", "safe_to_reconcile":"Безопасно провести",
            })
            view["Безопасно провести"] = view["Безопасно провести"].map({1: "Да", 0: "Нет"}).fillna("Нет")
            st.dataframe(
                view[["Артикул продавца", "Товар", "Место", "Основа сравнения", "Доступно WB", "К клиенту", "От клиента",
                      "Факт / контур", "В слоях", "Разница", "Статус", "Действие", "Безопасно провести", "Ставка, ₽", "Стоимость, ₽"]],
                hide_index=True, use_container_width=True, height=min(620, 92 + 34 * len(view)),
                column_config={
                    "Доступно WB": st.column_config.NumberColumn(format="%d"),
                    "К клиенту": st.column_config.NumberColumn(format="%d"),
                    "От клиента": st.column_config.NumberColumn(format="%d"),
                    "Факт / контур": st.column_config.NumberColumn(format="%d"),
                    "В слоях": st.column_config.NumberColumn(format="%d"),
                    "Разница": st.column_config.NumberColumn(format="%d"),
                    "Ставка, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Стоимость, ₽": st.column_config.NumberColumn(format="%.2f"),
                },
            )
            st.download_button(
                "Скачать диагностическую сверку CSV",
                data=view.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"fifo_reconciliation_diagnostic_{today_msk.isoformat()}.csv",
                mime="text/csv", use_container_width=True,
            )

            manual_allowed = bool(fifo_guard.get("manual_reconciliation_allowed", False))
            safe_rows = recon_preview[pd.to_numeric(recon_preview.get("safe_to_reconcile", 0), errors="coerce").fillna(0).astype(int).eq(1)]
            unsafe_rows = recon_preview[pd.to_numeric(recon_preview.get("safe_to_reconcile", 0), errors="coerce").fillna(0).astype(int).ne(1)]
            if not unsafe_rows.empty:
                st.error(
                    f"{int(pd.to_numeric(unsafe_rows['difference_units'], errors='coerce').fillna(0).abs().sum())} ед. относятся к WB-диагностике. "
                    "Они исключены из кнопки управленческой сверки и не будут списаны из FIFO даже при программном вызове функции."
                )
            if manual_allowed and not safe_rows.empty:
                st.warning("Доступна только безопасная локальная сверка (готово у вас / локально в пути). WB-строки в неё не входят.")
            else:
                st.info(
                    "Ручная сверка сейчас заблокирована. Для WB дождитесь восстановления детализации/документов по утрате или компенсации; "
                    "для локальных строк сначала выполните полную перепроверку."
                )
            confirm_recon = st.checkbox(
                "Подтверждаю безопасные локальные строки сверки",
                key="confirm_fifo_reconciliation", disabled=not manual_allowed,
            )
            recon_note = st.text_input(
                "Примечание к локальной сверке", placeholder="Например: подтверждён локальный физический остаток",
                disabled=not manual_allowed,
            )
            if st.button(
                "Провести безопасную локальную сверку FIFO", type="primary", use_container_width=True,
                disabled=(not manual_allowed) or (not confirm_recon) or int(sales_control.get("errors", 0) or 0) > 0,
            ):
                try:
                    result = apply_finished_goods_reconciliation(recon_note)
                    if int(result.get("run_id", 0) or 0) <= 0:
                        st.info(
                            f"Безопасных строк для проведения нет. WB-строк пропущено: {int(result.get('skipped_lines',0) or 0)}, "
                            f"единиц: {int(result.get('skipped_units',0) or 0)}."
                        )
                    else:
                        st.success(
                            f"Локальная сверка №{int(result.get('run_id',0))} проведена: строк {int(result.get('lines',0))}, "
                            f"досоздано {int(result.get('added_units',0))} ед., списано {int(result.get('removed_units',0))} ед.; "
                            f"WB-строк безопасно пропущено {int(result.get('skipped_lines',0) or 0)}."
                        )
                    st.cache_data.clear(); st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        st.markdown("#### История проведённых локальных сверок")
        recon_runs = read_fifo_reconciliation_runs(100)
        if recon_runs.empty:
            st.info("Проведённых сверок пока нет.")
        else:
            st.dataframe(
                recon_runs[["id", "created_at", "snapshot_at", "articles", "lines", "added_units", "removed_units", "adjustment_amount_rub", "note"]],
                hide_index=True, use_container_width=True, height=320,
                column_config={
                    "id": st.column_config.NumberColumn("Сверка", format="%d"),
                    "created_at": "Проведена", "snapshot_at": "Снимок WB",
                    "articles": st.column_config.NumberColumn("Артикулов", format="%d"),
                    "lines": st.column_config.NumberColumn("Строк", format="%d"),
                    "added_units": st.column_config.NumberColumn("Досоздано", format="%d"),
                    "removed_units": st.column_config.NumberColumn("Списано", format="%d"),
                    "adjustment_amount_rub": st.column_config.NumberColumn("Стоимость, ₽", format="%.2f"),
                    "note": "Примечание",
                },
            )
            selected_run = st.selectbox(
                "Открыть сверку", recon_runs["id"].astype(int).tolist(),
                format_func=lambda value: f"Сверка №{value}", key="fifo_recon_run_select",
            )
            run_lines = read_fifo_reconciliation_lines(int(selected_run))
            if not run_lines.empty:
                st.dataframe(
                    run_lines[["supplier_article", "product_name", "location", "physical_units", "layered_units", "difference_units", "action", "unit_cost_rub", "amount_rub", "note"]],
                    hide_index=True, use_container_width=True, height=360,
                    column_config={
                        "supplier_article":"Артикул продавца", "product_name":"Товар", "location":"Место",
                        "physical_units":st.column_config.NumberColumn("Физически",format="%d"),
                        "layered_units":st.column_config.NumberColumn("Было в слоях",format="%d"),
                        "difference_units":st.column_config.NumberColumn("Разница",format="%d"),
                        "action":"Действие", "unit_cost_rub":st.column_config.NumberColumn("Ставка, ₽",format="%.2f"),
                        "amount_rub":st.column_config.NumberColumn("Стоимость, ₽",format="%.2f"), "note":"Примечание",
                    },
                )


    with stock_tabs[5]:
        st.markdown("### Инциденты WB — диагностика и подтверждённые утраты")
        st.caption(
            "Этот модуль не зависит от переключателя периода сверху: он сравнивает последний детальный складской снимок "
            "с текущим контуром WB. Диагностический разрыв не является фактом утраты и ничего не списывает автоматически."
        )

        incident_diag = read_wb_incident_reconciliation()
        incident_summary = wb_incident_financial_summary()
        if incident_diag.empty:
            st.info("Недостаточно данных для посквозной диагностики инцидента: нужен детальный исторический и текущий складской снимок.")
        else:
            baseline_at = str(incident_diag.iloc[0].get("baseline_snapshot_at", "") or "")[:19]
            current_at = str(incident_diag.iloc[0].get("current_snapshot_at", "") or "")[:19]
            incident_ctx = wb_fifo_reconciliation_context()
            candidate_total = int(pd.to_numeric(incident_diag.get("incident_candidate_units", 0), errors="coerce").fillna(0).sum())
            unconfirmed_total = int(pd.to_numeric(incident_diag.get("unconfirmed_candidate_units", 0), errors="coerce").fillna(0).sum())
            safe_post_total = int(pd.to_numeric(incident_diag.get("safe_post_now_units", 0), errors="coerce").fillna(0).sum())
            blocked_by_layers_total = int(pd.to_numeric(incident_diag.get("candidate_blocked_by_layers_units", 0), errors="coerce").fillna(0).sum())
            layer_shortfall_total = int(pd.to_numeric(incident_diag.get("current_layer_shortfall_units", 0), errors="coerce").fillna(0).sum())
            restoration_needed_total = int(pd.to_numeric(incident_diag.get("layer_restoration_needed_units", 0), errors="coerce").fillna(0).sum())
            inferred_inflow_floor_total = int(pd.to_numeric(incident_diag.get("inferred_unregistered_inflow_floor_units", 0), errors="coerce").fillna(0).sum())
            confirmed_fbw_total = int(pd.to_numeric(incident_diag.get("confirmed_fbw_receipts", 0), errors="coerce").fillna(0).sum())
            if "global_external_gap_units" in incident_diag.columns and not incident_diag.empty:
                external_total = int(pd.to_numeric(incident_diag["global_external_gap_units"], errors="coerce").fillna(0).max())
            else:
                external_total = int(incident_ctx.get("diagnostic_external_gap_units", 0) or 0)
            exposure_total = int(incident_ctx.get("incident_contour_units", 0) or 0)
            candidate_cost = float(pd.to_numeric(incident_diag.get("incident_candidate_cost_rub", 0), errors="coerce").fillna(0).sum())
            safe_post_cost = float(pd.to_numeric(incident_diag.get("safe_post_now_cost_rub", 0), errors="coerce").fillna(0).sum())

            icards = st.columns(6)
            with icards[0]: kpi_card("Внешний разрыв", num(external_total), "Исторический ориентир")
            with icards[1]: kpi_card("Экспозиция", num(exposure_total), "На затронутых складах")
            with icards[2]: kpi_card("Кандидат", num(candidate_total), "Распределено по SKU")
            with icards[3]: kpi_card("Безопасно сейчас", num(safe_post_total), money(safe_post_cost))
            with icards[4]: kpi_card("Заблокировано слоями", num(blocked_by_layers_total), "Из текущего кандидата")
            with icards[5]: kpi_card("Подтверждено", num(int(incident_summary.get("confirmed_loss_units", 0) or 0)), money(float(incident_summary.get("confirmed_loss_cost_rub", 0) or 0)))

            safety_cards = st.columns(5)
            with safety_cards[0]: kpi_card("Контур выше FIFO", num(layer_shortfall_total), "Текущий дефицит слоёв")
            with safety_cards[1]: kpi_card("Подтвержд. FBW", num(confirmed_fbw_total), "Принятые поставки после базы")
            with safety_cards[2]: kpi_card("Мин. входящих по балансу", num(inferred_inflow_floor_total), "Ещё не объяснено")
            with safety_cards[3]: kpi_card("Нужно восстановить слоёв", num(restoration_needed_total), "До полного кандидата")
            with safety_cards[4]: kpi_card("Компенсации", money(float(incident_summary.get("compensation_rub", 0) or 0)), f"Результат {money(float(incident_summary.get('incident_result_rub', 0) or 0))}")

            st.info(
                f"База сравнения: детальный снимок {baseline_at}; текущий снимок {current_at}. "
                f"v6.3 включает в баланс {confirmed_fbw_total} ед. из подтверждённых API принятых FBW-поставок после базового снимка, "
                "а также ручные поступления Marketshelper. Продажи и возвраты учитываются по оперативному /supplier/sales. "
                "Неучтённые поставки/перемещения по-прежнему могут влиять на диагностический разрыв."
            )

            diag_view = incident_diag.copy()
            diag_view = diag_view[(diag_view["external_gap_units"] > 0) | (diag_view["incident_exposure_units"] > 0) | (diag_view["unexplained_increase_units"] > 0)].copy()
            diag_view = diag_view.rename(columns={
                "supplier_article":"Артикул продавца", "product_name":"Товар",
                "baseline_contour":"Контур 31.07", "incident_exposure_units":"Экспозиция",
                "incident_warehouses":"Затронутые склады", "sales_units":"Продажи после",
                "return_units":"Возвраты после", "manual_wb_receipts":"Ручные поступления",
                "confirmed_fbw_receipts":"Подтвержд. FBW", "registered_wb_receipts":"Всего учтено поступлений",
                "expected_without_external":"Ожидалось без внешнего выбытия", "current_contour":"Контур сейчас",
                "external_gap_units":"Сырой разрыв SKU", "unexplained_increase_units":"Необъяснённый рост",
                "incident_candidate_raw_units":"Сырой кандидат", "incident_candidate_units":"Распределённый ориентир", "confirmed_loss_units":"Подтверждено",
                "unconfirmed_candidate_units":"Осталось подтвердить", "fifo_wb_units":"FIFO сейчас",
                "fifo_gap_units":"FIFO − контур", "safe_fifo_capacity_units":"Резерв FIFO над контуром",
                "safe_post_now_units":"Безопасно списать сейчас", "candidate_blocked_by_layers_units":"Заблокировано слоями",
                "current_layer_shortfall_units":"Контур выше FIFO", "inferred_unregistered_inflow_floor_units":"Мин. входящих по балансу",
                "layer_restoration_needed_units":"Требует восстановления слоёв", "fifo_avg_rate":"Оценочная ставка, ₽",
                "incident_candidate_cost_rub":"Оценка кандидата, ₽", "safe_post_now_cost_rub":"Безопасная оценка, ₽", "diagnostic_status":"Статус",
            })
            diag_cols = [
                "Артикул продавца","Товар","Затронутые склады","Контур 31.07","Экспозиция",
                "Продажи после","Возвраты после","Ручные поступления","Подтвержд. FBW","Всего учтено поступлений","Ожидалось без внешнего выбытия",
                "Контур сейчас","Сырой разрыв SKU","Необъяснённый рост","Сырой кандидат","Распределённый ориентир","Подтверждено",
                "Осталось подтвердить","FIFO сейчас","FIFO − контур","Резерв FIFO над контуром","Безопасно списать сейчас",
                "Заблокировано слоями","Контур выше FIFO","Мин. входящих по балансу","Требует восстановления слоёв",
                "Оценочная ставка, ₽","Оценка кандидата, ₽","Безопасная оценка, ₽","Статус",
            ]
            st.dataframe(
                diag_view[diag_cols], hide_index=True, use_container_width=True,
                height=min(650, 105 + 34 * max(1, len(diag_view))),
                column_config={
                    col: st.column_config.NumberColumn(format="%d") for col in [
                        "Контур 31.07","Экспозиция","Продажи после","Возвраты после","Ручные поступления","Подтвержд. FBW","Всего учтено поступлений",
                        "Ожидалось без внешнего выбытия","Контур сейчас","Сырой разрыв SKU","Необъяснённый рост",
                        "Сырой кандидат","Распределённый ориентир","Подтверждено","Осталось подтвердить","FIFO сейчас","FIFO − контур",
                        "Резерв FIFO над контуром","Безопасно списать сейчас","Заблокировано слоями","Контур выше FIFO",
                        "Мин. входящих по балансу","Требует восстановления слоёв",
                    ]
                } | {
                    "Оценочная ставка, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Оценка кандидата, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Безопасная оценка, ₽": st.column_config.NumberColumn(format="%.2f"),
                },
            )
            st.download_button(
                "Скачать диагностику инцидента CSV",
                data=diag_view.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"wb_incident_diagnostic_{today_msk.isoformat()}.csv",
                mime="text/csv", use_container_width=True,
            )
            st.caption(
                f"Суммарная оценочная стоимость распределённого ориентира: {money(candidate_cost)}. Это не бухгалтерский убыток: "
                f"{external_total} ед. текущего исторического ориентира распределяются по SKU пропорционально диагностическому сигналу. "
                "Колонка «Безопасно списать сейчас» дополнительно ограничивает кандидат текущим запасом FIFO над полным контуром WB."
            )

            st.markdown("#### Реконструкция недостающих входящих / стоимостных слоёв")
            st.warning(
                "v6.3 уже вычитает подтверждённые API поставки FBW из необъяснённого притока, но не создаёт для них FIFO-слои автоматически. "
                "«Мин. входящих по балансу» теперь означает только остаток входящих движений, который не объясняется ни ручными поступлениями, ни подтверждёнными FBW-поставками. "
                "Стоимость новых слоёв всё ещё требует отдельного источника себестоимости, поэтому "
                "автодосоздание могло бы исказить будущий COGS. Полный кандидат утраты разблокируется только после появления достаточных слоёв."
            )
            restoration_view = incident_diag[
                (pd.to_numeric(incident_diag.get("layer_restoration_needed_units", 0), errors="coerce").fillna(0) > 0)
                | (pd.to_numeric(incident_diag.get("inferred_unregistered_inflow_floor_units", 0), errors="coerce").fillna(0) > 0)
            ].copy()
            if restoration_view.empty:
                st.success("SKU, требующих реконструкции слоёв, сейчас нет.")
            else:
                restoration_view = restoration_view.rename(columns={
                    "supplier_article":"Артикул продавца", "product_name":"Товар",
                    "current_contour":"Контур сейчас", "fifo_wb_units":"FIFO сейчас",
                    "current_layer_shortfall_units":"Текущий дефицит FIFO",
                    "confirmed_fbw_receipts":"Подтвержд. FBW",
                    "inferred_unregistered_inflow_floor_units":"Мин. входящих по балансу",
                    "unconfirmed_candidate_units":"Кандидат к подтверждению",
                    "safe_post_now_units":"Безопасно списать сейчас",
                    "candidate_blocked_by_layers_units":"Кандидат заблокирован",
                    "layer_restoration_needed_units":"Всего восстановить слоёв",
                    "diagnostic_status":"Причина",
                })
                restoration_cols = [
                    "Артикул продавца","Товар","Контур сейчас","FIFO сейчас","Текущий дефицит FIFO","Подтвержд. FBW",
                    "Мин. входящих по балансу","Кандидат к подтверждению","Безопасно списать сейчас",
                    "Кандидат заблокирован","Всего восстановить слоёв","Причина",
                ]
                st.dataframe(
                    restoration_view[restoration_cols], hide_index=True, use_container_width=True,
                    height=min(480, 105 + 34 * max(1, len(restoration_view))),
                    column_config={
                        col: st.column_config.NumberColumn(format="%d") for col in [
                            "Контур сейчас","FIFO сейчас","Текущий дефицит FIFO","Подтвержд. FBW","Мин. входящих по балансу",
                            "Кандидат к подтверждению","Безопасно списать сейчас","Кандидат заблокирован","Всего восстановить слоёв",
                        ]
                    },
                )
                st.download_button(
                    "Скачать план реконструкции CSV",
                    data=restoration_view[restoration_cols].to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"wb_fifo_layer_reconstruction_{today_msk.isoformat()}.csv",
                    mime="text/csv", use_container_width=True,
                )
                st.caption(
                    f"Сейчас физический контур выше FIFO суммарно на {layer_shortfall_total} ед.; минимум входящих по балансу — "
                    f"{inferred_inflow_floor_total} ед. Ещё {blocked_by_layers_total} ед. текущего кандидата утраты заблокированы до восстановления слоёв. "
                    f"Для полного проведения текущего кандидата модель требует восстановить до {restoration_needed_total} стоимостных единиц по SKU. "
                    "Эти величины не являются автоматическими поступлениями и сами базу не меняют."
                )

            st.markdown("#### Подтверждённые поставки FBW и реконструкция входящих")
            st.caption(
                "v6.3 сохраняет результат проверки FBW API в локальной базе. Поставка со статусом 5 («принято») и factDate после базового снимка "
                "становится подтверждённым входящим движением по количеству из состава поставки. Это меняет диагностику остатков, но само по себе не создаёт FIFO-стоимость."
            )
            supply_flash = st.session_state.pop("wb_supply_reconstruction_flash", "")
            if supply_flash:
                st.success(supply_flash)
            verified_persisted = read_verified_fbw_supplies()
            if not verified_persisted.empty:
                persisted_after = verified_persisted[
                    (pd.to_numeric(verified_persisted.get("accepted", 0), errors="coerce").fillna(0).astype(int) == 1)
                    & (verified_persisted.get("fact_date_msk", "").astype(str) > str(baseline_at))
                ].copy()
                if not persisted_after.empty:
                    total_units_persisted = int(pd.to_numeric(persisted_after.get("units", 0), errors="coerce").fillna(0).sum())
                    st.success(f"В базе подтверждено {len(persisted_after)} принятых поставок после базового снимка: {total_units_persisted} ед. Они уже включены в расчёт выше.")
                    persisted_view = persisted_after.rename(columns={
                        "supply_id":"incomeID / ID поставки","fact_date":"factDate","status_id":"Статус ID",
                        "warehouse_name":"Склад","sku_count":"SKU","units":"Ед.","verified_at":"Проверено",
                    })
                    st.dataframe(persisted_view[["incomeID / ID поставки","factDate","Статус ID","Склад","SKU","Ед.","Проверено"]],
                                 hide_index=True,use_container_width=True)
                    persisted_goods = read_verified_fbw_supply_goods()
                    if not persisted_goods.empty:
                        persisted_goods = persisted_goods[persisted_goods["supply_id"].isin(persisted_after["supply_id"].astype(int).tolist())].copy()
                        with st.expander("Состав подтверждённых поставок, уже учтённый в реконструкции", expanded=False):
                            pg = persisted_goods.rename(columns={
                                "supply_id":"incomeID","nm_id":"nmID","supplier_article":"Артикул",
                                "quantity":"Подтвержд. входящие, ед.","fact_date":"factDate","warehouse_name":"Склад",
                            })
                            st.dataframe(pg[["incomeID","factDate","Склад","nmID","Артикул","Подтвержд. входящие, ед."]], hide_index=True, use_container_width=True)

            st.markdown("#### v6.4 — ретроспективная реконструкция FIFO (preview)")
            st.caption(
                "Этот блок ничего не записывает в FIFO. Он берёт последний детальный снимок WB как исторический якорь, "
                "достраивает полный контур на эту дату (включая товары к клиенту и от клиента), вставляет подтверждённые FBW-поставки "
                "на их фактические factDate и заново проигрывает уже известные продажи/возвраты по времени."
            )
            try:
                retro_summary, retro_rows = preview_retroactive_fbw_fifo_reconstruction()
            except Exception as exc:
                retro_summary, retro_rows = {"ready": False, "reason": str(exc)}, pd.DataFrame()

            if not retro_summary.get("ready"):
                st.info(f"Preview пока недоступен: {retro_summary.get('reason','недостаточно данных')}.")
            else:
                retro_cards = st.columns(6)
                with retro_cards[0]: kpi_card("Контур на базе", num(int(retro_summary.get("starting_fifo_units",0) or 0)), f"до моста FIFO {num(int(retro_summary.get('starting_fifo_units_before_bridge',0) or 0))}")
                with retro_cards[1]: kpi_card("Мост полного контура", f"+{num(int(retro_summary.get('baseline_contour_bridge_units',0) or 0))}", "preview, стоимость оценочная")
                with retro_cards[2]: kpi_card("Подтвержд. FBW", f"+{num(int(retro_summary.get('verified_supply_units',0) or 0))}", f"{int(retro_summary.get('verified_supply_count',0) or 0)} поставки")
                with retro_cards[3]: kpi_card("Replay продаж", num(int(retro_summary.get("replayed_sales",0) or 0)), f"возвратов {num(int(retro_summary.get('replayed_returns',0) or 0))}")
                with retro_cards[4]: kpi_card("FIFO после replay", num(int(retro_summary.get("preview_fifo_units",0) or 0)), f"сейчас {num(int(retro_summary.get('current_fifo_units',0) or 0))}")
                with retro_cards[5]: kpi_card("FIFO − контур", num(int(retro_summary.get("preview_fifo_minus_contour",0) or 0)), f"контур сейчас {num(int(retro_summary.get('current_contour_units',0) or 0))}")

                retro_safety = st.columns(6)
                with retro_safety[0]: kpi_card("Кандидат утраты", num(int(retro_summary.get("candidate_units",0) or 0)), "диагностический")
                with retro_safety[1]: kpi_card("Безопасно сейчас", num(int(retro_summary.get("current_safe_post_units",0) or 0)), "до реконструкции")
                with retro_safety[2]: kpi_card("Безопасно preview", num(int(retro_summary.get("preview_safe_post_units",0) or 0)), "после исторического replay")
                with retro_safety[3]: kpi_card("Заблокировано preview", num(int(retro_summary.get("preview_blocked_units",0) or 0)), "кандидат без слоёв")
                with retro_safety[4]: kpi_card("Осталось восстановить", num(int(retro_summary.get("preview_restoration_needed_units",0) or 0)), "после preview")
                with retro_safety[5]: kpi_card("Временных слоёв", num(int(retro_summary.get("synthetic_units_needed",0) or 0)), "нужно при replay")

                replay_start_label = str(retro_summary.get("replay_start", "") or "")[:19]
                earliest_supply_label = str(retro_summary.get("earliest_verified_supply_at", "") or "")[:19]
                st.info(
                    f"Исторический replay начинается со снимка {replay_start_label}; первая подтверждённая поставка — {earliest_supply_label}. "
                    f"Старый FIFO сейчас содержит {num(int(retro_summary.get('current_fifo_units',0) or 0))} ед.; preview даёт "
                    f"{num(int(retro_summary.get('preview_fifo_units',0) or 0))} ед. Разница {num(int(retro_summary.get('fifo_units_delta',0) or 0))} ед. "
                    "возникает не простым прибавлением поставок, а после полного хронологического переигрывания продаж и возвратов."
                )
                cogs_delta = float(retro_summary.get("replay_cogs_delta_rub", 0) or 0)
                st.warning(
                    f"Количество и даты FBW-поставок подтверждены API. В v6.5 preview для стоимости используется лучший доступный исторический cost basis "
                    f"с явной маркировкой источника и надёжности. Оценочная стоимость мостового слоя — {money(float(retro_summary.get('baseline_contour_bridge_provisional_cost_rub',0) or 0))}, "
                    f"поставок — {money(float(retro_summary.get('verified_supply_provisional_cost_rub',0) or 0))}. "
                    f"Предварительное изменение COGS replay: {money(cogs_delta)}. НИ ОДИН слой в основной БД этим блоком не создаётся и не изменяется."
                )
                for warning in retro_summary.get("warnings", []) or []:
                    st.caption(str(warning))

                if not retro_rows.empty:
                    retro_view = retro_rows.copy()
                    retro_view = retro_view[
                        (pd.to_numeric(retro_view.get("confirmed_fbw_units",0), errors="coerce").fillna(0) > 0)
                        | (pd.to_numeric(retro_view.get("baseline_contour_bridge_units",0), errors="coerce").fillna(0) > 0)
                        | (pd.to_numeric(retro_view.get("incident_candidate_units",0), errors="coerce").fillna(0) > 0)
                        | (pd.to_numeric(retro_view.get("preview_layer_shortfall_units",0), errors="coerce").fillna(0) > 0)
                    ].copy()
                    retro_view = retro_view.rename(columns={
                        "supplier_article":"Артикул продавца", "product_name":"Товар",
                        "baseline_contour_bridge_units":"Мост базы, ед.", "confirmed_fbw_units":"FBW подтверждено, ед.",
                        "provisional_supply_rate_rub":"Оценочная ставка, ₽", "replay_sales":"Replay продаж",
                        "replay_returns":"Replay возвратов", "synthetic_units_needed":"Временных слоёв",
                        "current_fifo_units":"FIFO сейчас", "preview_fifo_units":"FIFO preview", "fifo_units_delta":"Δ FIFO",
                        "current_contour_units":"Контур сейчас", "incident_candidate_units":"Кандидат утраты",
                        "current_safe_post_units":"Безопасно сейчас", "preview_safe_post_units":"Безопасно preview",
                        "safe_post_delta_units":"Δ безопасно", "current_blocked_units":"Блок сейчас", "preview_blocked_units":"Блок preview",
                        "preview_layer_shortfall_units":"Дефицит preview", "current_replay_cogs_rub":"COGS сейчас, ₽",
                        "preview_replay_cogs_rub":"COGS preview, ₽", "replay_cogs_delta_rub":"Δ COGS, ₽", "status":"Статус",
                    })
                    retro_cols = [
                        "Артикул продавца","Товар","Мост базы, ед.","FBW подтверждено, ед.","Replay продаж","Replay возвратов",
                        "FIFO сейчас","FIFO preview","Δ FIFO","Контур сейчас","Кандидат утраты","Безопасно сейчас","Безопасно preview",
                        "Δ безопасно","Блок сейчас","Блок preview","Дефицит preview","Временных слоёв","Оценочная ставка, ₽",
                        "COGS сейчас, ₽","COGS preview, ₽","Δ COGS, ₽","Статус",
                    ]
                    st.dataframe(
                        retro_view[retro_cols], hide_index=True, use_container_width=True,
                        height=min(560, 105 + 34 * max(1, len(retro_view))),
                        column_config={
                            col: st.column_config.NumberColumn(format="%d") for col in [
                                "Мост базы, ед.","FBW подтверждено, ед.","Replay продаж","Replay возвратов","FIFO сейчас","FIFO preview","Δ FIFO",
                                "Контур сейчас","Кандидат утраты","Безопасно сейчас","Безопасно preview","Δ безопасно","Блок сейчас","Блок preview",
                                "Дефицит preview","Временных слоёв",
                            ]
                        } | {
                            "Оценочная ставка, ₽": st.column_config.NumberColumn(format="%.2f"),
                            "COGS сейчас, ₽": st.column_config.NumberColumn(format="%.2f"),
                            "COGS preview, ₽": st.column_config.NumberColumn(format="%.2f"),
                            "Δ COGS, ₽": st.column_config.NumberColumn(format="%.2f"),
                        },
                    )
                    st.download_button(
                        "Скачать preview ретроспективного FIFO CSV",
                        data=retro_view[retro_cols].to_csv(index=False).encode("utf-8-sig"),
                        file_name=f"wb_fifo_retro_preview_{today_msk.isoformat()}.csv",
                        mime="text/csv", use_container_width=True,
                    )
                    st.caption(
                        "v6.4 — только dry-run. Кнопки применения реконструкции намеренно нет: сначала проверяем хронологию и итог по SKU, "
                        "а затем отдельно определяем надёжный источник себестоимости для мостового слоя и подтверждённых FBW-поставок."
                    )

            st.markdown("#### v6.5 — реконструкция себестоимости исторических слоёв")
            st.caption(
                "v6.5 не применяет реконструкцию. Для каждой единицы мостового слоя и подтверждённой FBW-поставки он выбирает "
                "лучший доступный источник ставки на соответствующую историческую дату: сначала фактическая закрытая производственная партия, "
                "затем несинтетический исторический FIFO, затем базовая/прогнозная ставка. Источник и уровень надёжности показываются явно."
            )
            if retro_summary.get("ready"):
                basis_units = int(retro_summary.get("cost_basis_units", 0) or 0)
                actual_units = int(retro_summary.get("cost_basis_actual_units", 0) or 0)
                hist_units = int(retro_summary.get("cost_basis_historical_fifo_units", 0) or 0)
                baseline_units = int(retro_summary.get("cost_basis_baseline_units", 0) or 0)
                fallback_units = int(retro_summary.get("cost_basis_forecast_units", 0) or 0)
                missing_units = int(retro_summary.get("cost_basis_missing_units", 0) or 0)
                bcards = st.columns(6)
                with bcards[0]: kpi_card("Cost basis", num(basis_units), money(float(retro_summary.get("cost_basis_amount_rub", 0) or 0)))
                with bcards[1]: kpi_card("Факт партий", num(actual_units), "Высокая надёжность")
                with bcards[2]: kpi_card("Исторический FIFO", num(hist_units), "Средняя надёжность")
                with bcards[3]: kpi_card("Базовая ставка", num(baseline_units), "Оценка")
                with bcards[4]: kpi_card("Forecast / fallback", num(fallback_units), "Низкая надёжность")
                with bcards[5]: kpi_card("Без ставки", num(missing_units), "Должно быть 0 перед Apply")

                if actual_units < basis_units:
                    st.warning(
                        f"Фактические закрытые производственные партии покрывают {num(actual_units)} из {num(basis_units)} реконструируемых единиц. "
                        f"Исторический FIFO покрывает {num(hist_units)} ед. Это более надёжно, чем просто текущая ставка, но всё ещё не является фактом конкретной производственной партии. "
                        "Поэтому Apply Reconstruction в v6.5 намеренно отсутствует."
                    )
                elif basis_units > 0:
                    st.success("Все реконструируемые единицы имеют фактическую себестоимость закрытых производственных партий.")
                if missing_units > 0:
                    st.error(f"Для {num(missing_units)} ед. себестоимость не найдена. Применять историческую реконструкцию нельзя.")

                if not retro_rows.empty:
                    cost_view = retro_rows[
                        (pd.to_numeric(retro_rows.get("baseline_contour_bridge_units", 0), errors="coerce").fillna(0) > 0)
                        | (pd.to_numeric(retro_rows.get("confirmed_fbw_units", 0), errors="coerce").fillna(0) > 0)
                    ].copy()
                    if not cost_view.empty:
                        cost_view = cost_view.rename(columns={
                            "supplier_article":"Артикул продавца", "product_name":"Товар",
                            "baseline_contour_bridge_units":"Мост базы, ед.", "bridge_cost_rate_rub":"Ставка моста, ₽",
                            "bridge_cost_source":"Источник моста", "bridge_cost_confidence":"Надёжность моста",
                            "bridge_cost_evidence_date":"Дата доказательства моста",
                            "confirmed_fbw_units":"FBW, ед.", "provisional_supply_rate_rub":"Ставка FBW, ₽",
                            "supply_cost_source":"Источник FBW", "supply_cost_confidence":"Надёжность FBW",
                            "supply_cost_evidence_date":"Дата доказательства FBW", "supply_cost_detail":"Поставки",
                            "forecast_total_cost_rub":"Текущий forecast, ₽",
                        })
                        cost_cols = [
                            "Артикул продавца","Товар","Мост базы, ед.","Ставка моста, ₽","Источник моста","Надёжность моста","Дата доказательства моста",
                            "FBW, ед.","Ставка FBW, ₽","Источник FBW","Надёжность FBW","Дата доказательства FBW","Поставки","Текущий forecast, ₽",
                        ]
                        for col in cost_cols:
                            if col not in cost_view.columns:
                                cost_view[col] = "" if col not in {"Мост базы, ед.","Ставка моста, ₽","FBW, ед.","Ставка FBW, ₽","Текущий forecast, ₽"} else 0
                        st.dataframe(
                            cost_view[cost_cols], hide_index=True, use_container_width=True,
                            height=min(520, 105 + 34 * max(1, len(cost_view))),
                            column_config={
                                "Мост базы, ед.": st.column_config.NumberColumn(format="%d"),
                                "FBW, ед.": st.column_config.NumberColumn(format="%d"),
                                "Ставка моста, ₽": st.column_config.NumberColumn(format="%.2f"),
                                "Ставка FBW, ₽": st.column_config.NumberColumn(format="%.2f"),
                                "Текущий forecast, ₽": st.column_config.NumberColumn(format="%.2f"),
                            },
                        )
                        st.download_button(
                            "Скачать cost basis v6.5 CSV",
                            data=cost_view[cost_cols].to_csv(index=False).encode("utf-8-sig"),
                            file_name=f"wb_fifo_cost_basis_v65_{today_msk.isoformat()}.csv",
                            mime="text/csv", use_container_width=True,
                        )

                    residual = retro_rows[pd.to_numeric(retro_rows.get("preview_layer_shortfall_units", 0), errors="coerce").fillna(0) > 0].copy()
                    st.markdown("##### Остаточные количественные расхождения после replay")
                    if residual.empty:
                        st.success("После replay нет SKU, у которых текущий физический контур выше реконструированного FIFO.")
                    else:
                        residual = residual.rename(columns={
                            "supplier_article":"Артикул продавца", "product_name":"Товар",
                            "current_contour_units":"Контур сейчас", "preview_fifo_units":"FIFO preview",
                            "preview_layer_shortfall_units":"Необъяснённый приток, ед.", "confirmed_fbw_units":"Подтвержд. FBW, ед.",
                        })
                        residual_cols = ["Артикул продавца","Товар","Контур сейчас","FIFO preview","Необъяснённый приток, ед.","Подтвержд. FBW, ед."]
                        st.dataframe(
                            residual[residual_cols].sort_values("Необъяснённый приток, ед.", ascending=False),
                            hide_index=True, use_container_width=True,
                            column_config={c: st.column_config.NumberColumn(format="%d") for c in residual_cols[2:]},
                        )
                        st.info(
                            f"Всего остаётся {num(int(retro_summary.get('preview_layer_shortfall_units', 0) or 0))} ед. по SKU. "
                            "Это не себестоимость и не подтверждённая утрата: это количество, для которого после полного replay всё ещё не найдено входящее движение."
                        )
            else:
                st.info("Cost basis станет доступен после готовности ретроспективного replay v6.4.")

            st.markdown("#### v6.6 — финальный аудит доказательств перед Apply")
            st.caption(
                "Этот аудит ничего не меняет в FIFO. Он локализует последнюю единицу без cost basis и отдельно ищет incomeID, "
                "которые могли быть пропущены старым фильтром, потому что сам ID существовал до 31.07, но один из остаточных SKU "
                "появился в нём уже после базового снимка."
            )
            try:
                audit_summary, audit_missing_cost, audit_residual, audit_income = read_wb_final_evidence_audit()
            except Exception as exc:
                audit_summary, audit_missing_cost, audit_residual, audit_income = {"ready": False, "reason": str(exc)}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

            if not audit_summary.get("ready"):
                st.info(f"Финальный аудит пока недоступен: {audit_summary.get('reason','недостаточно данных')}.")
            else:
                acards = st.columns(6)
                with acards[0]: kpi_card("Без cost basis", num(int(audit_summary.get("missing_cost_units",0) or 0)), f"SKU {int(audit_summary.get('missing_cost_skus',0) or 0)}")
                with acards[1]: kpi_card("Остаточный приток", num(int(audit_summary.get("residual_units",0) or 0)), f"SKU {int(audit_summary.get('residual_skus',0) or 0)}")
                with acards[2]: kpi_card("Новых incomeID-кандидатов", num(int(audit_summary.get("high_priority_unverified_income_ids",0) or 0)), "нужно проверить API")
                with acards[3]: kpi_card("Apply готов", "Да" if audit_summary.get("apply_ready") else "Нет", "только при 0/0")
                with acards[4]: kpi_card("База аудита", str(audit_summary.get("baseline_snapshot_at", ""))[:10], "последний детальный снимок")
                with acards[5]: kpi_card("Режим", "READ ONLY", "ничего не списывает")

                st.markdown("##### Единицы без себестоимости")
                if audit_missing_cost.empty:
                    st.success("Все реконструируемые единицы имеют хотя бы один cost basis.")
                else:
                    mc = audit_missing_cost.rename(columns={
                        "nm_id":"nmID", "supplier_article":"Артикул", "product_name":"Товар",
                        "missing_cost_units":"Без ставки, ед.", "baseline_contour_bridge_units":"Мост базы, ед.",
                        "confirmed_fbw_units":"FBW, ед.", "baseline_available":"Доступно 31.07",
                        "baseline_to_client":"К клиенту 31.07", "baseline_from_client":"От клиента 31.07",
                        "baseline_chrt_ids":"chrtID", "baseline_warehouses":"Склад 31.07",
                        "current_available":"Доступно сейчас", "current_to_client":"К клиенту сейчас",
                        "current_from_client":"От клиента сейчас", "catalog_present":"Есть в каталоге",
                        "cost_present":"Есть ставка в costs", "operational_events":"Заказы/продажи",
                    })
                    mc_cols = [
                        "nmID","Артикул","Товар","Без ставки, ед.","Мост базы, ед.","FBW, ед.",
                        "Доступно 31.07","К клиенту 31.07","От клиента 31.07","chrtID","Склад 31.07",
                        "Доступно сейчас","К клиенту сейчас","От клиента сейчас","Есть в каталоге","Есть ставка в costs","Заказы/продажи",
                    ]
                    for col in mc_cols:
                        if col not in mc.columns:
                            mc[col] = ""
                    st.dataframe(mc[mc_cols], hide_index=True, use_container_width=True)
                    st.error(
                        "Эта строка должна быть разрешена до фактического Apply. Если у неё нет артикула/каталога и она всё время находится только в пути, "
                        "её нельзя автоматически наделять ставкой другого товара."
                    )

                st.markdown("##### Остаточные SKU и расширенный поиск `incomeID`")
                if audit_residual.empty:
                    st.success("После replay количественных SKU-расхождений не осталось.")
                else:
                    ar = audit_residual.rename(columns={
                        "supplier_article":"Артикул", "product_name":"Товар", "current_contour_units":"Контур сейчас",
                        "preview_fifo_units":"FIFO preview", "preview_layer_shortfall_units":"Необъяснённый приток, ед.",
                        "confirmed_fbw_units":"Уже подтверждено FBW, ед.",
                    })
                    st.dataframe(ar[["Артикул","Товар","Контур сейчас","FIFO preview","Необъяснённый приток, ед.","Уже подтверждено FBW, ед."]], hide_index=True, use_container_width=True)

                if audit_income.empty:
                    st.info("Для остаточных SKU дополнительных incomeID-сигналов в локальной истории не найдено.")
                else:
                    ai = audit_income.rename(columns={
                        "income_id":"incomeID", "priority":"Приоритет", "new_residual_skus":"SKU впервые после базы",
                        "active_residual_skus":"Активные остаточные SKU", "first_residual_event_at":"Первое событие",
                        "last_residual_event_at":"Последнее событие", "orders_after_baseline":"Заказы после базы",
                        "sales_after_baseline":"Продажи после базы", "returns_after_baseline":"Возвраты после базы",
                        "verified":"Проверен API", "verified_fact_date":"factDate", "verified_status_id":"Статус ID",
                        "verified_residual_units":"Ед. остаточных SKU в поставке", "warehouse_name":"Склад",
                    })
                    ai_cols = [
                        "incomeID","Приоритет","SKU впервые после базы","Активные остаточные SKU","Первое событие","Последнее событие",
                        "Заказы после базы","Продажи после базы","Возвраты после базы","Проверен API","factDate","Статус ID","Склад","Ед. остаточных SKU в поставке",
                    ]
                    st.dataframe(ai[ai_cols], hide_index=True, use_container_width=True, height=min(420, 105 + 34 * max(1, len(ai))))

                    ids_to_audit = [int(x) for x in audit_income.loc[audit_income["priority"] == "Проверить API", "income_id"].tolist()[:10]]
                    if ids_to_audit:
                        st.warning(
                            "Найден как минимум один incomeID, который старый фильтр мог пропустить: сам ID начал встречаться раньше базы, "
                            "но один или несколько из 35 остаточных SKU впервые появились в нём уже после 31.07. Проверяем factDate напрямую через FBW API."
                        )
                        if st.button(f"Проверить {len(ids_to_audit)} кандид. incomeID через FBW API", use_container_width=True, key="wb_v66_verify_residual_income"):
                            token = get_token()
                            if not token:
                                st.error("Сначала сохраните токен WB API в Настройках.")
                            else:
                                api = WBAPI(token)
                                checked = []
                                saved = 0
                                baseline_dt = pd.to_datetime(audit_summary.get("baseline_snapshot_at", ""), errors="coerce")
                                progress = st.progress(0.0, text="Проверяю финальные incomeID-кандидаты…")
                                for pos, income_id in enumerate(ids_to_audit, start=1):
                                    try:
                                        detail = api.get_fbw_supply_details(income_id)
                                        time.sleep(2.1)
                                        goods = api.get_fbw_supply_goods(income_id)
                                        persisted = save_verified_fbw_supply(income_id, detail, goods)
                                        fact_dt = pd.to_datetime(persisted.get("fact_date_msk", ""), errors="coerce")
                                        after = bool(pd.notna(fact_dt) and pd.notna(baseline_dt) and fact_dt > baseline_dt)
                                        checked.append({
                                            "incomeID": income_id,
                                            "factDate": persisted.get("fact_date_msk", ""),
                                            "Статус ID": persisted.get("status_id", 0),
                                            "Склад": persisted.get("warehouse_name", ""),
                                            "SKU": persisted.get("sku_count", 0),
                                            "Ед.": persisted.get("units", 0),
                                            "После базы": "Да" if after else "Нет",
                                            "Принято": "Да" if persisted.get("accepted") else "Нет",
                                            "Ошибка": "",
                                        })
                                        saved += 1
                                    except Exception as exc:
                                        checked.append({"incomeID":income_id,"factDate":"","Статус ID":"","Склад":"","SKU":0,"Ед.":0,"После базы":"Не проверено","Принято":"Нет","Ошибка":str(exc)[:240]})
                                    progress.progress(pos / max(1, len(ids_to_audit)), text=f"Проверено {pos} из {len(ids_to_audit)}")
                                    if pos < len(ids_to_audit):
                                        time.sleep(2.1)
                                progress.empty()
                                st.session_state["wb_v66_audit_results"] = checked
                                if saved:
                                    st.session_state["wb_supply_reconstruction_flash"] = (
                                        f"v6.6 проверил и сохранил {saved} дополнительн. incomeID. Replay и финальный аудит пересчитаны."
                                    )
                                    st.rerun()
                    checked = st.session_state.get("wb_v66_audit_results") or []
                    if checked:
                        st.dataframe(pd.DataFrame(checked), hide_index=True, use_container_width=True)

            st.markdown("#### v6.7 — Apply Reconstruction")
            st.caption(
                "Это первый этап, который реально меняет FIFO. Перед записью v6.7 автоматически создаёт консистентную SQLite-копию, "
                "повторно строит тот же replay под проверками, атомарно заменяет только пост-якорные FIFO-слои/allocations и пересчитывает COGS уже известных продаж/возвратов. "
                "Кандидат складской утраты здесь НЕ списывается."
            )
            try:
                v67_ready, v67_suspense, v67_blockers = read_fifo_reconstruction_v67_readiness()
                v67_runs = read_fifo_reconstruction_apply_runs(10)
            except Exception as exc:
                v67_ready, v67_suspense, v67_blockers, v67_runs = {"ready": False, "reason": str(exc)}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

            v67cards = st.columns(6)
            with v67cards[0]: kpi_card("Apply", "ГОТОВ" if v67_ready.get("ready") else ("ПРИМЕНЁН" if int(v67_ready.get("already_applied_run_id",0) or 0)>0 else "СТОП"), "атомарная транзакция")
            with v67cards[1]: kpi_card("FIFO после Apply", num(int(v67_ready.get("expected_fifo_units",0) or 0)), f"сейчас {num(int(v67_ready.get('current_fifo_units',0) or 0))}")
            with v67cards[2]: kpi_card("Мост базы", num(int(v67_ready.get("bridge_units",0) or 0)), "исторический полный контур")
            with v67cards[3]: kpi_card("Подтвержд. FBW", num(int(v67_ready.get("verified_supply_units",0) or 0)), "принятые API-поставки")
            with v67cards[4]: kpi_card("Cost pending", num(int(v67_ready.get("suspense_units",0) or 0)), "не попадает в COGS")
            with v67cards[5]: kpi_card("Кандидат утраты", num(int(v67_ready.get("candidate_loss_units",0) or 0)), "НЕ списывается Apply")

            if not v67_suspense.empty:
                sp = v67_suspense.rename(columns={
                    "nm_id":"nmID", "supplier_article":"Артикул", "product_name":"Товар", "missing_cost_units":"Cost pending, ед.",
                    "baseline_warehouses":"Склад базы", "baseline_to_client":"К клиенту на базе", "current_to_client":"К клиенту сейчас",
                    "suspense_reason":"Почему разрешено",
                })
                show_cols = [c for c in ["nmID","Артикул","Товар","Cost pending, ед.","Склад базы","К клиенту на базе","К клиенту сейчас","Почему разрешено"] if c in sp.columns]
                st.warning(
                    "v6.7 допускает cost_pending только для узкого suspense-исключения: единица без каталога/ставки и без продаж, которая остаётся в WB-контуре вне доступного остатка. "
                    "Такой слой блокируется для автоматического COGS: при будущей продаже Marketshelper выдаст ошибку вместо synthetic-ставки."
                )
                st.dataframe(sp[show_cols], hide_index=True, use_container_width=True)
            if not v67_blockers.empty:
                st.error("Есть строки без допустимого cost basis. Apply заблокирован.")
                st.dataframe(v67_blockers, hide_index=True, use_container_width=True)

            applied_run_id = int(v67_ready.get("already_applied_run_id",0) or 0)
            if v67_ready.get("ready"):
                expected_phrase = f"APPLY {int(v67_ready.get('candidate_loss_units',0) or 0)}"
                st.success(
                    f"Все жёсткие проверки пройдены: residual={int(v67_ready.get('residual_units',0) or 0)}, synthetic={int(v67_ready.get('synthetic_units',0) or 0)}, "
                    f"hard cost blockers={int(v67_ready.get('hard_blocker_units',0) or 0)}. Перед Apply будет создан отдельный backup базы."
                )
                st.info(
                    f"Replay перепишет FIFO-историю от {v67_ready.get('baseline_snapshot_at','')} до текущего снимка, включая "
                    f"{num(int(v67_ready.get('replay_sales',0) or 0))} продаж и {num(int(v67_ready.get('replay_returns',0) or 0))} возвратов. "
                    f"Диагностический кандидат внешнего выбытия {num(int(v67_ready.get('candidate_loss_units',0) or 0))} ед. останется отдельным и не будет признан утратой."
                )
                v67_ack = st.checkbox(
                    "Я понимаю: Apply меняет FIFO-слои и FIFO-COGS исторических продаж, но не списывает складские утраты.",
                    key="wb_v67_apply_ack",
                )
                v67_text = st.text_input(
                    f"Для применения введите точно: {expected_phrase}", key="wb_v67_apply_phrase", placeholder=expected_phrase
                )
                if st.button(
                    "Создать backup и применить реконструкцию FIFO",
                    type="primary", use_container_width=True, key="wb_v67_apply_button",
                    disabled=not (v67_ack and str(v67_text or "").strip().upper() == expected_phrase.upper()),
                ):
                    try:
                        with st.spinner("Создаю backup, применяю replay и проверяю инварианты перед COMMIT…"):
                            applied = apply_retroactive_fifo_reconstruction_v67(v67_text)
                        st.session_state["wb_v67_flash"] = (
                            f"v6.7 применён: run #{applied.get('run_id')}; FIFO {num(int(applied.get('fifo_units',0) or 0))} ед.; "
                            f"cost_pending {num(int(applied.get('cost_pending_units',0) or 0))} ед. Backup: {applied.get('backup_path','')}"
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Apply отменён без частичной записи: {exc}")
            elif applied_run_id > 0:
                flash = st.session_state.pop("wb_v67_flash", "")
                if flash:
                    st.success(flash)
                latest_applied = v67_runs[(v67_runs["id"] == applied_run_id)] if not v67_runs.empty and "id" in v67_runs.columns else pd.DataFrame()
                st.success(
                    f"Реконструкция уже применена: run #{applied_run_id}. Текущий FIFO должен соответствовать историческому replay. "
                    "Кандидат складской утраты остаётся диагностическим до отдельного подтверждения инцидента."
                )
                if not latest_applied.empty:
                    r = latest_applied.iloc[0]
                    st.caption(
                        f"Backup перед Apply: {r.get('backup_path','')} · применено: {r.get('applied_at','')} · plan hash: {str(r.get('plan_hash',''))[:16]}…"
                    )
                with st.expander("Аварийный полный откат v6.7", expanded=False):
                    st.warning(
                        "Откат восстанавливает ВСЮ SQLite-базу из автоматической копии, созданной непосредственно перед Apply. "
                        "Все изменения данных после Apply тоже будут потеряны. Перед откатом v6.7 дополнительно сохраняет safety-backup текущего состояния."
                    )
                    rb_phrase = f"ROLLBACK {applied_run_id}"
                    rb_text = st.text_input(f"Для отката введите точно: {rb_phrase}", key="wb_v67_rollback_phrase", placeholder=rb_phrase)
                    if st.button(
                        "Откатить базу к состоянию до Apply",
                        use_container_width=True, key="wb_v67_rollback_button",
                        disabled=str(rb_text or "").strip().upper() != rb_phrase.upper(),
                    ):
                        try:
                            with st.spinner("Создаю safety-backup текущего состояния и восстанавливаю pre-Apply SQLite…"):
                                rolled = rollback_retroactive_fifo_reconstruction_v67(applied_run_id, rb_text)
                            st.success(f"Откат выполнен. Восстановлено из {rolled.get('restored_from','')}")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Откат не выполнен: {exc}")
            else:
                st.error(f"Apply пока заблокирован. {v67_ready.get('reason','')}".strip())
                st.caption(
                    f"Residual: {v67_ready.get('residual_units','?')} · synthetic: {v67_ready.get('synthetic_units','?')} · "
                    f"hard cost blockers: {v67_ready.get('hard_blocker_units','?')} · непроверенных incomeID: {v67_ready.get('high_priority_income_ids','?')}"
                )

            if not v67_runs.empty:
                with st.expander("Журнал Apply / rollback", expanded=False):
                    run_view = v67_runs.rename(columns={
                        "id":"Run", "status":"Статус", "expected_fifo_units":"Ожидалось FIFO", "applied_fifo_units":"Применено FIFO",
                        "candidate_loss_units":"Кандидат утраты", "cost_pending_units":"Cost pending", "backup_path":"Backup",
                        "created_at":"Создан", "applied_at":"Применён", "rolled_back_at":"Откат",
                    })
                    cols = [c for c in ["Run","Статус","Ожидалось FIFO","Применено FIFO","Кандидат утраты","Cost pending","Создан","Применён","Откат","Backup"] if c in run_view.columns]
                    st.dataframe(run_view[cols], hide_index=True, use_container_width=True)

            st.markdown("##### Поиск новых `incomeID` в оперативной истории")
            supply_evidence = read_wb_income_supply_evidence()
            if supply_evidence.empty:
                st.info("После базового снимка не найдено новых `incomeID`, впервые появившихся в локальной истории заказов/продаж.")
            else:
                evidence_view = supply_evidence.rename(columns={
                    "income_id":"incomeID / ID поставки", "first_observed_at":"Впервые замечен",
                    "last_observed_at":"Последнее событие", "sku_count":"SKU",
                    "orders_seen_units":"Заказов замечено", "sales_seen_units":"Продаж замечено",
                    "returns_seen_units":"Возвратов замечено", "articles":"Артикулы",
                })
                evidence_cols = [
                    "incomeID / ID поставки","Впервые замечен","Последнее событие","SKU",
                    "Заказов замечено","Продаж замечено","Возвратов замечено","Артикулы",
                ]
                st.dataframe(evidence_view[evidence_cols], hide_index=True, use_container_width=True,
                             height=min(300, 105 + 34 * max(1, len(evidence_view))))

                if st.button("Проверить эти ID через API поставок FBW", use_container_width=True, key="wb_verify_income_supplies"):
                    token = get_token()
                    if not token:
                        st.error("Сначала сохраните токен WB API в Настройках.")
                    else:
                        api = WBAPI(token)
                        verified_rows = []
                        goods_rows = []
                        saved_count = 0
                        accepted_saved_units = 0
                        ids_to_check = [int(x) for x in supply_evidence["income_id"].tolist()[:10]]
                        progress = st.progress(0.0, text="Проверяю поставки FBW…")
                        def _wb_msk_ts(value):
                            ts = pd.to_datetime(value, errors="coerce")
                            if pd.isna(ts):
                                return pd.NaT
                            # Local DB snapshots are stored in Moscow time without an offset,
                            # while WB API may return ISO timestamps with an explicit offset.
                            # Normalize both sides to Europe/Moscow before comparing them.
                            if getattr(ts, "tzinfo", None) is None:
                                return ts.tz_localize("Europe/Moscow")
                            return ts.tz_convert("Europe/Moscow")

                        baseline_dt = _wb_msk_ts(baseline_at)
                        for pos, income_id in enumerate(ids_to_check, start=1):
                            try:
                                detail = api.get_fbw_supply_details(income_id)
                                time.sleep(2.1)
                                goods = api.get_fbw_supply_goods(income_id)
                                fact_date = str(detail.get("factDate") or detail.get("fact_date") or "")
                                create_date = str(detail.get("createDate") or detail.get("createdDate") or detail.get("create_date") or "")
                                fact_dt = _wb_msk_ts(fact_date)
                                confirmed_after = bool(pd.notna(fact_dt) and pd.notna(baseline_dt) and fact_dt > baseline_dt)
                                warehouse = str(detail.get("warehouseName") or detail.get("warehouse") or detail.get("warehouseID") or "")
                                status_id = detail.get("statusID") if detail.get("statusID") is not None else detail.get("statusId")
                                declared_units = 0
                                declared_skus = set()
                                for item in goods:
                                    try:
                                        nm_id = int(item.get("nmID") or item.get("nmId") or 0)
                                    except (TypeError, ValueError):
                                        nm_id = 0
                                    if nm_id > 0:
                                        declared_skus.add(nm_id)
                                    qty = 0
                                    barcodes = item.get("barcodes") or []
                                    if isinstance(barcodes, list) and barcodes:
                                        for barcode_row in barcodes:
                                            if isinstance(barcode_row, dict):
                                                try:
                                                    qty += int(barcode_row.get("quantity") or 0)
                                                except (TypeError, ValueError):
                                                    pass
                                    else:
                                        try:
                                            qty = int(item.get("quantity") or 0)
                                        except (TypeError, ValueError):
                                            qty = 0
                                    declared_units += max(0, qty)
                                    goods_rows.append({
                                        "incomeID": income_id,
                                        "nmID": nm_id,
                                        "Артикул": str(item.get("vendorCode") or item.get("supplierArticle") or ""),
                                        "Количество в составе": max(0, qty),
                                    })
                                persisted_supply = save_verified_fbw_supply(income_id, detail, goods)
                                saved_count += 1
                                if bool(persisted_supply.get("accepted")) and confirmed_after:
                                    accepted_saved_units += int(persisted_supply.get("units", 0) or 0)
                                verified_rows.append({
                                    "incomeID / ID поставки": income_id,
                                    "factDate": fact_date,
                                    "createDate": create_date,
                                    "Статус ID": status_id,
                                    "Склад": warehouse,
                                    "SKU в составе": len(declared_skus),
                                    "Ед. в составе": declared_units,
                                    "Приёмка после базового снимка": "Да" if confirmed_after else "Нет / не подтверждено",
                                    "Ошибка": "",
                                })
                            except Exception as exc:
                                verified_rows.append({
                                    "incomeID / ID поставки": income_id,
                                    "factDate": "", "createDate": "", "Статус ID": "", "Склад": "",
                                    "SKU в составе": 0, "Ед. в составе": 0,
                                    "Приёмка после базового снимка": "Не проверено",
                                    "Ошибка": str(exc)[:240],
                                })
                            progress.progress(pos / max(1, len(ids_to_check)), text=f"Проверено {pos} из {len(ids_to_check)}")
                            if pos < len(ids_to_check):
                                time.sleep(2.1)
                        progress.empty()
                        st.session_state["wb_verified_fbw_supplies"] = verified_rows
                        st.session_state["wb_verified_fbw_supply_goods"] = goods_rows
                        if saved_count > 0:
                            st.session_state["wb_supply_reconstruction_flash"] = (
                                f"Сохранено проверенных поставок: {saved_count}. Принятые после базового снимка: {accepted_saved_units} ед. "
                                "Диагностика инцидента пересчитана с подтверждёнными входящими."
                            )
                            st.rerun()

                verified_rows = st.session_state.get("wb_verified_fbw_supplies") or []
                if verified_rows:
                    verified_df = pd.DataFrame(verified_rows)
                    st.dataframe(verified_df, hide_index=True, use_container_width=True)
                    confirmed_count = int((verified_df["Приёмка после базового снимка"] == "Да").sum())
                    if confirmed_count:
                        st.success(
                            f"API подтвердил {confirmed_count} поставк(и/ок) с factDate после базового снимка. "
                            "Эти поставки сохранены в базе v6.3 и включены в количественную реконструкцию. "
                            "FIFO-слои по-прежнему не создаются автоматически, пока не определена их себестоимость."
                        )
                    else:
                        st.warning(
                            "Пока ни одна кандидатная поставка не подтверждена API как принятая после базового снимка. "
                            "Если в колонке «Ошибка» есть 401/403, токену может не хватать доступа к поставкам FBW."
                        )
                    goods_rows = st.session_state.get("wb_verified_fbw_supply_goods") or []
                    if goods_rows:
                        with st.expander("Состав проверенных поставок", expanded=False):
                            st.dataframe(pd.DataFrame(goods_rows), hide_index=True, use_container_width=True)
                            st.caption(
                                "Количество здесь — состав принятой поставки, возвращённый API. Для поставок со статусом 5 и factDate после базового снимка "
                                "v6.3 использует эти единицы как подтверждённые входящие в количественной реконструкции, но не создаёт стоимостный FIFO-слой."
                            )

        st.markdown("#### Реестр инцидентов и документов")
        with st.expander("Создать карточку инцидента", expanded=False):
            case_name = st.text_input("Название инцидента", placeholder="Например: пожар / утрата товара на складе WB", key="wb_incident_case_name")
            case_date = st.date_input("Дата инцидента", value=today_msk, key="wb_incident_case_date")
            case_warehouse = st.selectbox(
                "Склад / группа", ["", "Тула / Алексин", "Екатеринбург — Перспективная", "Коледино", "Новосемейкино", "Владимир WB", "Несколько складов / другое"],
                key="wb_incident_case_warehouse",
            )
            case_doc = st.text_input("Документ / основание (можно добавить позже)", placeholder="Номер акта, уведомления, компенсационного документа", key="wb_incident_case_doc")
            case_note = st.text_area("Примечание", key="wb_incident_case_note")
            if st.button("Создать карточку инцидента", use_container_width=True, key="wb_incident_case_create"):
                try:
                    case_id = create_wb_incident_case(case_name, case_date.isoformat(), case_warehouse, case_doc, case_note)
                    st.success(f"Карточка инцидента №{case_id} создана.")
                    st.cache_data.clear(); st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        cases = read_wb_incident_cases(100)
        if cases.empty:
            st.info("Подтверждённых или зарегистрированных инцидентов пока нет. Диагностика выше остаётся только наблюдением.")
        else:
            st.dataframe(
                cases[["id","incident_date","incident_name","warehouse_label","document_ref","status","confirmed_loss_units","confirmed_loss_cost_rub","compensation_rub","incident_result_rub"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "id": st.column_config.NumberColumn("№", format="%d"), "incident_date":"Дата",
                    "incident_name":"Инцидент", "warehouse_label":"Склад", "document_ref":"Основание", "status":"Статус",
                    "confirmed_loss_units":st.column_config.NumberColumn("Утрата, ед.",format="%d"),
                    "confirmed_loss_cost_rub":st.column_config.NumberColumn("FIFO-стоимость, ₽",format="%.2f"),
                    "compensation_rub":st.column_config.NumberColumn("Компенсация, ₽",format="%.2f"),
                    "incident_result_rub":st.column_config.NumberColumn("Результат, ₽",format="%.2f"),
                },
            )
            case_ids = cases["id"].astype(int).tolist()
            selected_case_id = st.selectbox(
                "Открыть карточку", case_ids,
                format_func=lambda cid: f"№{cid} — {str(cases.loc[cases['id'].astype(int).eq(cid),'incident_name'].iloc[0])}",
                key="wb_incident_case_select",
            )
            selected_case = cases[cases["id"].astype(int).eq(int(selected_case_id))].iloc[0]
            case_summary = wb_incident_financial_summary(int(selected_case_id))
            csum = st.columns(4)
            with csum[0]: kpi_card("Подтверждено", num(int(case_summary.get("confirmed_loss_units",0) or 0)), "единиц")
            with csum[1]: kpi_card("Стоимость утраты", money(float(case_summary.get("confirmed_loss_cost_rub",0) or 0)), "Не COGS продаж")
            with csum[2]: kpi_card("Компенсация WB", money(float(case_summary.get("compensation_rub",0) or 0)), "По внесённым документам")
            with csum[3]: kpi_card("Результат инцидента", money(float(case_summary.get("incident_result_rub",0) or 0)), "Компенсация − FIFO-стоимость")

            with st.expander("Обновить документ / основание", expanded=not bool(str(selected_case.get("document_ref","") or "").strip())):
                upd_doc = st.text_input("Документ / основание", value=str(selected_case.get("document_ref","") or ""), key=f"wb_incident_doc_{selected_case_id}")
                upd_note = st.text_area("Примечание к карточке", value=str(selected_case.get("note","") or ""), key=f"wb_incident_note_{selected_case_id}")
                if st.button("Сохранить карточку", use_container_width=True, key=f"wb_incident_update_{selected_case_id}"):
                    try:
                        update_wb_incident_case(int(selected_case_id), document_ref=upd_doc, note=upd_note)
                        st.success("Карточка обновлена.")
                        st.cache_data.clear(); st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

            st.markdown("##### Подтверждение утраты товара")
            st.warning(
                "Списание доступно только при заполненном документе/основании и только в пределах безопасного резерва FIFO над текущим полным контуром WB. "
                "Даже прямой вызов функции в v6.3 не позволит опустить FIFO ниже контура. Утрата проводится отдельным движением «wb_incident_loss», "
                "не попадает в COGS продаж и не пересчитывает исторические продажи."
            )
            if incident_diag.empty:
                st.info("Диагностических строк для выбора нет.")
            else:
                loss_candidates = incident_diag[pd.to_numeric(incident_diag.get("unconfirmed_candidate_units", 0), errors="coerce").fillna(0) > 0].copy()
                if loss_candidates.empty:
                    st.info("Нет неподтверждённых SKU-кандидатов.")
                else:
                    nm_options = loss_candidates["nm_id"].astype(int).tolist()
                    selected_nm = st.selectbox(
                        "Артикул для подтверждения", nm_options,
                        format_func=lambda nm: (
                            f"{str(loss_candidates.loc[loss_candidates['nm_id'].astype(int).eq(nm),'supplier_article'].iloc[0])} — "
                            f"кандидат {int(loss_candidates.loc[loss_candidates['nm_id'].astype(int).eq(nm),'unconfirmed_candidate_units'].iloc[0])} ед.; "
                            f"безопасно {int(loss_candidates.loc[loss_candidates['nm_id'].astype(int).eq(nm),'safe_post_now_units'].iloc[0])} ед."
                        ), key=f"wb_incident_nm_{selected_case_id}",
                    )
                    cand = loss_candidates[loss_candidates["nm_id"].astype(int).eq(int(selected_nm))].iloc[0]
                    max_fifo = max(0, int(cand.get("fifo_wb_units",0) or 0))
                    current_contour = max(0, int(cand.get("current_contour",0) or 0))
                    candidate_remaining = max(0, int(cand.get("unconfirmed_candidate_units",0) or 0))
                    safe_capacity = max(0, int(cand.get("safe_fifo_capacity_units",0) or 0))
                    safe_now = max(0, int(cand.get("safe_post_now_units",0) or 0))
                    blocked_units = max(0, int(cand.get("candidate_blocked_by_layers_units",0) or 0))
                    layer_shortfall = max(0, int(cand.get("current_layer_shortfall_units",0) or 0))
                    restoration_needed = max(0, int(cand.get("layer_restoration_needed_units",0) or 0))
                    suggested = safe_now
                    loss_qty = st.number_input(
                        "Подтверждённое количество, ед.", min_value=1, max_value=max(1,safe_now), value=max(1,suggested), step=1,
                        disabled=safe_now <= 0, key=f"wb_incident_qty_{selected_case_id}_{selected_nm}",
                    )
                    st.caption(
                        f"Кандидат: {candidate_remaining} ед.; безопасно списать сейчас: {safe_now} ед.; текущий FIFO WB: {max_fifo} ед.; "
                        f"полный контур WB: {current_contour} ед.; резерв FIFO над контуром: {safe_capacity} ед.; экспозиция на затронутых складах: "
                        f"{int(cand.get('incident_exposure_units',0) or 0)} ед."
                    )
                    if blocked_units > 0 or layer_shortfall > 0:
                        st.warning(
                            f"Для этого SKU {blocked_units} ед. кандидата пока заблокированы. Текущий дефицит FIFO относительно контура: "
                            f"{layer_shortfall} ед.; до полного проведения кандидата требуется восстановить {restoration_needed} стоимостных слоёв. "
                            "v6.3 не создаёт их автоматически без достоверного источника себестоимости."
                        )
                    loss_note = st.text_input("Примечание к строке утраты", key=f"wb_incident_loss_note_{selected_case_id}_{selected_nm}")
                    confirm_loss = st.checkbox(
                        "Подтверждаю, что количество взято из документа/официального основания WB, а не из диагностического расчёта",
                        key=f"wb_incident_confirm_{selected_case_id}_{selected_nm}",
                    )
                    has_doc = bool(str(selected_case.get("document_ref","") or "").strip())
                    if st.button(
                        "Провести безопасную подтверждённую утрату в FIFO", type="primary", use_container_width=True,
                        disabled=(not has_doc) or (not confirm_loss) or safe_now <= 0,
                        key=f"wb_incident_apply_loss_{selected_case_id}_{selected_nm}",
                    ):
                        try:
                            result = apply_wb_incident_loss(int(selected_case_id), int(selected_nm), int(loss_qty), loss_note)
                            st.success(
                                f"Утрата проведена: {int(result.get('units',0))} ед.; FIFO-стоимость {money(float(result.get('fifo_cost_rub',0) or 0))}. "
                                f"FIFO после операции: {int(result.get('fifo_after_units',0))} ед.; текущий контур WB: {int(result.get('current_contour_units',0))} ед. "
                                "COGS продаж не изменён."
                            )
                            st.cache_data.clear(); st.rerun()
                        except Exception as exc:
                            st.error(str(exc))

            st.markdown("##### Компенсация WB")
            comp_amount = st.number_input("Сумма компенсации, ₽", min_value=0.0, value=0.0, step=100.0, key=f"wb_incident_comp_amount_{selected_case_id}")
            comp_date = st.date_input("Дата компенсации", value=today_msk, key=f"wb_incident_comp_date_{selected_case_id}")
            comp_ref = st.text_input("Документ / идентификатор компенсации", key=f"wb_incident_comp_ref_{selected_case_id}")
            comp_note = st.text_input("Примечание к компенсации", key=f"wb_incident_comp_note_{selected_case_id}")
            if st.button("Записать компенсацию", use_container_width=True, disabled=comp_amount <= 0 or not comp_ref.strip(), key=f"wb_incident_comp_add_{selected_case_id}"):
                try:
                    comp_id = record_wb_incident_compensation(int(selected_case_id), float(comp_amount), comp_date.isoformat(), comp_ref, comp_note)
                    st.success(f"Компенсация записана, строка №{comp_id}.")
                    st.cache_data.clear(); st.rerun()
                except Exception as exc:
                    st.error(str(exc))

            losses = read_wb_incident_loss_lines(int(selected_case_id))
            comps = read_wb_incident_compensations(int(selected_case_id))
            if not losses.empty:
                st.markdown("##### Подтверждённые строки утраты")
                st.dataframe(
                    losses[["id","created_at","supplier_article","product_name","confirmed_units","fifo_cost_rub","unit_cost_rub","status","note"]],
                    hide_index=True, use_container_width=True,
                    column_config={
                        "id":st.column_config.NumberColumn("Строка",format="%d"), "created_at":"Проведено",
                        "supplier_article":"Артикул", "product_name":"Товар",
                        "confirmed_units":st.column_config.NumberColumn("Ед.",format="%d"),
                        "fifo_cost_rub":st.column_config.NumberColumn("FIFO-стоимость, ₽",format="%.2f"),
                        "unit_cost_rub":st.column_config.NumberColumn("Ставка, ₽",format="%.2f"),
                        "status":"Статус", "note":"Примечание",
                    },
                )
                active_losses = losses[losses["status"].astype(str).eq("applied")]
                if not active_losses.empty:
                    with st.expander("Отменить ошибочно проведённую строку", expanded=False):
                        reverse_line = st.selectbox("Строка", active_losses["id"].astype(int).tolist(), key=f"wb_incident_reverse_line_{selected_case_id}")
                        reverse_confirm = st.checkbox("Подтверждаю отмену строки утраты и восстановление FIFO-слоёв", key=f"wb_incident_reverse_confirm_{selected_case_id}")
                        if st.button("Отменить строку", disabled=not reverse_confirm, key=f"wb_incident_reverse_btn_{selected_case_id}"):
                            result = reverse_wb_incident_loss(int(reverse_line))
                            if result.get("ok"):
                                st.success(str(result.get("message","Операция отменена.")))
                                st.cache_data.clear(); st.rerun()
                            else:
                                st.error(str(result.get("message","Не удалось отменить операцию.")))
            if not comps.empty:
                st.markdown("##### Записанные компенсации")
                st.dataframe(
                    comps[["id","compensation_date","amount_rub","source_ref","note"]], hide_index=True, use_container_width=True,
                    column_config={
                        "id":st.column_config.NumberColumn("Строка",format="%d"), "compensation_date":"Дата",
                        "amount_rub":st.column_config.NumberColumn("Сумма, ₽",format="%.2f"), "source_ref":"Документ", "note":"Примечание",
                    },
                )
                with st.expander("Удалить ошибочно внесённую компенсацию", expanded=False):
                    delete_comp_id = st.selectbox("Строка компенсации", comps["id"].astype(int).tolist(), key=f"wb_incident_delete_comp_{selected_case_id}")
                    delete_comp_confirm = st.checkbox("Подтверждаю удаление только ошибочной записи компенсации", key=f"wb_incident_delete_comp_confirm_{selected_case_id}")
                    if st.button("Удалить запись компенсации", disabled=not delete_comp_confirm, key=f"wb_incident_delete_comp_btn_{selected_case_id}"):
                        if delete_wb_incident_compensation(int(delete_comp_id)):
                            st.success("Запись компенсации удалена.")
                            st.cache_data.clear(); st.rerun()
                        else:
                            st.error("Запись не найдена.")

