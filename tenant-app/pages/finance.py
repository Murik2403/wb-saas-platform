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
    load_settings,
    save_settings,
)
from db import (
    read_production_cost_profiles,
    read_sales_fifo_cogs,
    read_suppliers,
)
from report_tools import (
    build_finance_excel,
    build_reconciliation,
    parse_income_expense_report,
)


def render(ctx: dict) -> None:
    data = ctx['data']
    end = ctx['end']
    start = ctx['start']

    st.markdown("### Финансовый результат")
    f = data.financial
    if float(f.get("report_rows", 0) or 0) <= 0:
        st.info("Финансовый отчёт ещё не загружен. Откройте «Настройки» и нажмите «Загрузить финансовый отчёт».")
    else:
        report_complete = bool(f.get("financial_period_complete", False))
        report_calc_end = str(f.get("financial_calculation_end", "") or "")
        report_scope_note = (
            "За весь выбранный период"
            if report_complete
            else (f"Предварительно · фин. данные по {report_calc_end}" if report_calc_end else "Финансовый отчёт недоступен")
        )

        c = st.columns(4)
        with c[0]:
            kpi_card(
                "Выкупы",
                money(float(f.get("operational_buyout_amount", 0) or 0)),
                "Оперативно /sales · продажи минус возвраты",
            )
        with c[1]:
            kpi_card("К перечислению за товар", money(f["to_pay"]), report_scope_note if not report_complete else "Комиссия WB уже учтена")
        with c[2]:
            kpi_card("Расходы WB", money(f["wb_expenses"]), report_scope_note if not report_complete else "Логистика, хранение и удержания")
        with c[3]:
            kpi_card("Прибыль до налога", money(f["profit"]), report_scope_note if not report_complete else "После рекламы и себестоимости")

        secondary = st.columns(5)
        with secondary[0]:
            kpi_card("Продажа по фин. отчётам", money(f["sales"]), report_scope_note if not report_complete else "Отдельная база расчёта WB")
        with secondary[1]:
            kpi_card("После расходов WB", money(f["after_wb"]), report_scope_note if not report_complete else "До рекламы и себестоимости")
        with secondary[2]:
            ad_subtitle = f"В фин. расчёте по {report_calc_end}" if not report_complete and report_calc_end else "За выбранный период"
            kpi_card("Реклама", money(float(f.get("financial_ad_spend", data.kpi["ad_spend"]) or 0)), ad_subtitle)
        with secondary[3]:
            kpi_card("Себестоимость", money(f["cost"]), f"Покрытие {f['cost_coverage']:.0f}%")
        with secondary[4]:
            op_sales = float(f.get("operational_sale_units", 0) or 0)
            op_returns = float(f.get("operational_return_units", 0) or 0)
            op_net = float(f.get("operational_net_units", 0) or 0)
            kpi_card("Продано нетто", num(op_net), f"Продажи {num(op_sales)} · возвраты {num(op_returns)}")

        operational_last_change = str(f.get("operational_last_change", "") or "")
        report_last_date = str(f.get("report_last_date", "") or "")
        operational_freshness = (
            f" Оперативные продажи обновлены WB по {operational_last_change}."
            if operational_last_change else ""
        )
        report_freshness = f" Финансовый отчёт актуален по {report_last_date}." if report_last_date else ""
        st.caption(
            "Источник «Выкупы» и количества продаж/возвратов — оперативный /supplier/sales. "
            "К перечислению, расходы WB, прибыль и товарная экономика — отчёт реализации WB."
            + operational_freshness + report_freshness
        )
        if not report_complete:
            selected_end_text = end.strftime("%d.%m.%Y")
            if report_calc_end:
                st.warning(
                    f"Финансовый отчёт WB пока покрывает выбранный период только по {report_calc_end}, "
                    f"а выбранная дата окончания — {selected_end_text}. Поэтому «К перечислению», расходы WB, "
                    "себестоимость и прибыль показаны как предварительные и рассчитаны только на доступном "
                    "финансовом горизонте. Реклама в расчёте прибыли автоматически ограничена тем же горизонтом."
                )
            else:
                st.warning("Финансовый отчёт WB пока не содержит данных для выбранного периода. Финансовые карточки не являются итоговыми.")

        # v4.4: comparison only. It never overwrites the accounting result.
        # Actual batch averages become available after real shifts are closed.
        actual_profiles = read_production_cost_profiles()
        actual_scenario = pd.DataFrame()
        actual_profit = float(f["profit"])
        actual_cost_total = float(f["cost"])
        # Coverage is an operational KPI: its denominator must match the same
        # /supplier/sales net units shown in the "Продано нетто" card, not the
        # realization report which can lag by a day or more. The profit scenario
        # itself remains accounting-based and therefore uses financial_products.
        actual_total_units = max(float(f.get("operational_net_units", 0) or 0), 0.0)
        actual_coverage_units = 0.0
        if actual_total_units > 0 and not data.products.empty and not actual_profiles.empty:
            coverage_ops = data.products[["Артикул WB", "Продажи"]].copy()
            coverage_ops["Артикул WB"] = pd.to_numeric(coverage_ops["Артикул WB"], errors="coerce").fillna(0).astype(int)
            coverage_ops["Продано нетто"] = pd.to_numeric(coverage_ops["Продажи"], errors="coerce").fillna(0).clip(lower=0)
            coverage_profiles = actual_profiles[["nm_id", "actual_unit_cost_rub"]].copy()
            coverage_profiles["nm_id"] = pd.to_numeric(coverage_profiles["nm_id"], errors="coerce").fillna(0).astype(int)
            coverage_profiles["actual_unit_cost_rub"] = pd.to_numeric(coverage_profiles["actual_unit_cost_rub"], errors="coerce").fillna(0.0)
            coverage_ops = coverage_ops.merge(coverage_profiles, left_on="Артикул WB", right_on="nm_id", how="left")
            actual_coverage_units = float(coverage_ops.loc[coverage_ops["actual_unit_cost_rub"].fillna(0) > 0, "Продано нетто"].sum())
            actual_coverage_units = min(actual_coverage_units, actual_total_units)
        if not data.financial_products.empty:
            actual_scenario = data.financial_products[[
                "Артикул WB", "Артикул продавца", "Товар", "Продано нетто",
                "Себестоимость ед.", "Себестоимость", "Расчётная прибыль"
            ]].copy()
            actual_scenario["Артикул WB"] = pd.to_numeric(actual_scenario["Артикул WB"], errors="coerce").fillna(0).astype(int)
            actual_scenario["Продано нетто"] = pd.to_numeric(actual_scenario["Продано нетто"], errors="coerce").fillna(0).clip(lower=0)
            if not actual_profiles.empty:
                profiles = actual_profiles[["nm_id", "actual_unit_cost_rub", "produced_units", "latest_batch_date"]].copy()
                profiles["nm_id"] = pd.to_numeric(profiles["nm_id"], errors="coerce").fillna(0).astype(int)
                actual_scenario = actual_scenario.merge(profiles, left_on="Артикул WB", right_on="nm_id", how="left")
            else:
                actual_scenario["actual_unit_cost_rub"] = 0.0
                actual_scenario["produced_units"] = 0.0
                actual_scenario["latest_batch_date"] = ""
            actual_scenario["actual_unit_cost_rub"] = pd.to_numeric(actual_scenario["actual_unit_cost_rub"], errors="coerce").fillna(0.0)
            actual_scenario["Факт. ставка, ₽"] = actual_scenario["actual_unit_cost_rub"].where(
                actual_scenario["actual_unit_cost_rub"] > 0, actual_scenario["Себестоимость ед."]
            )
            actual_scenario["Факт. себестоимость, ₽"] = actual_scenario["Продано нетто"] * actual_scenario["Факт. ставка, ₽"]
            actual_scenario["Изменение себестоимости, ₽"] = actual_scenario["Факт. себестоимость, ₽"] - actual_scenario["Себестоимость"]
            actual_scenario["Оценка прибыли по партиям, ₽"] = actual_scenario["Расчётная прибыль"] - actual_scenario["Изменение себестоимости, ₽"]
            actual_cost_total = float(actual_scenario["Факт. себестоимость, ₽"].sum())
            actual_profit = float(f["profit"]) - float(actual_scenario["Изменение себестоимости, ₽"].sum())

        st.markdown("### Оценка по фактической себестоимости партий")
        scenario_cols = st.columns(4)
        with scenario_cols[0]:
            kpi_card("Текущая прибыль", money(float(f["profit"])), "По фиксированной базовой себестоимости")
        with scenario_cols[1]:
            kpi_card("Оценка по партиям", money(actual_profit), "Не заменяет бухгалтерский результат")
        with scenario_cols[2]:
            kpi_card("Себестоимость по партиям", money(actual_cost_total), f"База: {money(float(f['cost']))}")
        with scenario_cols[3]:
            coverage_pct = actual_coverage_units / actual_total_units * 100 if actual_total_units else 0.0
            kpi_card("Покрытие фактом", f"{coverage_pct:.0f}%", f"{num(actual_coverage_units)} из {num(actual_total_units)} проданных единиц")
        if actual_profiles.empty or actual_coverage_units <= 0:
            st.info("Фактических закрытых производственных партий пока нет. После закрытия смены здесь появится сравнительный сценарий, а основной финансовый результат останется без изменений.")
        else:
            differences = actual_scenario[actual_scenario["actual_unit_cost_rub"] > 0].copy()
            differences = differences.reindex(differences["Изменение себестоимости, ₽"].abs().sort_values(ascending=False).index).head(12)
            st.dataframe(
                differences[["Артикул продавца", "Товар", "Продано нетто", "Себестоимость ед.", "Факт. ставка, ₽", "Изменение себестоимости, ₽", "Оценка прибыли по партиям, ₽", "latest_batch_date"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "Себестоимость ед.": st.column_config.NumberColumn("Базовая ставка, ₽", format="%.2f"),
                    "Факт. ставка, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Изменение себестоимости, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Оценка прибыли по партиям, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "latest_batch_date": "Последняя партия",
                },
            )
        st.caption(
            "Покрытие фактом считается по оперативным нетто-продажам /supplier/sales, поэтому его знаменатель совпадает с карточкой «Продано нетто». "
            "Сам сценарий прибыли использует средневзвешенную фактическую стоимость уже произведённых партий и финансовый отчёт WB. "
            "До сквозной привязки конкретной продажи к конкретной партии это управленческая оценка, а не бухгалтерский FIFO-COGS."
        )

        st.markdown("### FIFO-себестоимость продаж и возвратов")
        fifo_sales = read_sales_fifo_cogs(start.isoformat(), end.isoformat())
        fifo_profit_target = float(f["profit"])
        if fifo_sales.empty:
            st.info("За выбранный период нет операций продаж и возвратов для FIFO-оценки.")
        else:
            fifo_num_cols = [
                "sale_units", "return_units", "net_units", "baseline_cogs_rub", "estimated_fifo_cogs_rub",
                "exact_fifo_cogs_rub", "covered_events", "total_events", "error_events"
            ]
            for col in fifo_num_cols:
                fifo_sales[col] = pd.to_numeric(fifo_sales.get(col, 0), errors="coerce").fillna(0.0)
            total_events = float(fifo_sales["total_events"].sum())
            covered_events = float(fifo_sales["covered_events"].sum())
            baseline_fifo_cogs = float(fifo_sales["baseline_cogs_rub"].sum())
            estimated_fifo_cogs = float(fifo_sales["estimated_fifo_cogs_rub"].sum())
            exact_fifo_part = float(fifo_sales["exact_fifo_cogs_rub"].sum())
            fifo_delta = estimated_fifo_cogs - baseline_fifo_cogs
            fifo_profit = float(f["profit"]) - fifo_delta
            fifo_profit_target = fifo_profit
            fifo_coverage = covered_events / total_events * 100 if total_events else 0.0
            fifo_cards = st.columns(5)
            with fifo_cards[0]: kpi_card("Оценка COGS FIFO", money(estimated_fifo_cogs), f"Базовая оценка {money(baseline_fifo_cogs)}")
            with fifo_cards[1]: kpi_card("Точная FIFO-часть", money(exact_fifo_part), "Только операции после включения учёта")
            with fifo_cards[2]: kpi_card("Покрытие событий", f"{fifo_coverage:.0f}%", f"{num(covered_events)} из {num(total_events)} продаж/возвратов")
            with fifo_cards[3]: kpi_card("Изменение COGS", money(fifo_delta), "Относительно базовых ставок")
            with fifo_cards[4]: kpi_card("Прибыль по FIFO", money(fifo_profit), "Текущая прибыль с поправкой на FIFO")
            fifo_sales["Покрытие, %"] = fifo_sales.apply(
                lambda r: float(r["covered_events"]) / float(r["total_events"]) * 100 if float(r["total_events"]) else 0.0, axis=1
            )
            fifo_sales["Изменение COGS, ₽"] = fifo_sales["estimated_fifo_cogs_rub"] - fifo_sales["baseline_cogs_rub"]
            fifo_view = fifo_sales.rename(columns={
                "supplier_article": "Артикул продавца", "product_name": "Товар", "sale_units": "Продажи, шт",
                "return_units": "Возвраты, шт", "net_units": "Продано нетто", "baseline_cogs_rub": "Базовая COGS, ₽",
                "estimated_fifo_cogs_rub": "Оценка FIFO COGS, ₽", "exact_fifo_cogs_rub": "Точная FIFO-часть, ₽",
                "error_events": "Ошибки"
            })
            fifo_view = fifo_view.reindex(fifo_view["Изменение COGS, ₽"].abs().sort_values(ascending=False).index)
            st.dataframe(
                fifo_view[["Артикул продавца", "Товар", "Продажи, шт", "Возвраты, шт", "Продано нетто",
                           "Базовая COGS, ₽", "Оценка FIFO COGS, ₽", "Точная FIFO-часть, ₽",
                           "Изменение COGS, ₽", "Покрытие, %", "Ошибки"]],
                hide_index=True, use_container_width=True, height=min(520, 92 + 34 * len(fifo_view)),
                column_config={
                    "Продажи, шт": st.column_config.NumberColumn(format="%.0f"),
                    "Возвраты, шт": st.column_config.NumberColumn(format="%.0f"),
                    "Продано нетто": st.column_config.NumberColumn(format="%.0f"),
                    "Базовая COGS, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Оценка FIFO COGS, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Точная FIFO-часть, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Изменение COGS, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Покрытие, %": st.column_config.NumberColumn(format="%.1f%%"),
                    "Ошибки": st.column_config.NumberColumn(format="%.0f"),
                },
            )
            st.caption(
                "Операции, существовавшие до включения версии 4.6, оцениваются по базовой себестоимости. "
                "Новые продажи списывают конкретные FIFO-слои на WB, а возвраты восстанавливают слой исходной продажи, когда совпадает SRID."
            )


        st.markdown("### Маржа по каждому артикулу")
        st.caption(
            "Версия 5.2 учитывает полный горизонт пополнения: время до появления товара на WB плюс страховой запас. "
            "Для закупаемых товаров применяются MOQ и сроки поставки, а рекламные сокращения выполняются поэтапно. "
            "Операционные решения строятся по маржинальной экономике; чистая прибыль используется для сверки с итогом магазина."
        )
        decision_settings = load_settings()
        purchase_rules = decision_settings.get("decision_purchase_rules", {})
        if not isinstance(purchase_rules, dict):
            purchase_rules = {}
        with st.expander("Пороговые значения и логистика решений", expanded=False):
            threshold_cols = st.columns(4)
            with threshold_cols[0]:
                margin_target = st.number_input(
                    "Целевая маржа, %", min_value=0.0, max_value=80.0,
                    value=float(decision_settings.get("decision_margin_target", 15.0) or 15.0), step=1.0, key="article_margin_target"
                )
            with threshold_cols[1]:
                ad_share_limit = st.number_input(
                    "Максимальная доля рекламы, %", min_value=0.0, max_value=80.0,
                    value=float(decision_settings.get("decision_ad_share_limit", 12.0) or 12.0), step=1.0, key="article_ad_limit"
                )
            with threshold_cols[2]:
                return_limit = st.number_input(
                    "Допустимые возвраты, %", min_value=0.0, max_value=80.0,
                    value=float(decision_settings.get("decision_return_limit", 10.0) or 10.0), step=1.0, key="article_return_limit"
                )
            with threshold_cols[3]:
                stock_days_limit = st.number_input(
                    "Страховой запас после пополнения, дней", min_value=1.0, max_value=120.0,
                    value=float(decision_settings.get("decision_stock_days", 14.0) or 14.0), step=1.0, key="article_stock_limit"
                )
            logistics_cols = st.columns(3)
            with logistics_cols[0]:
                production_wb_lead_days = st.number_input(
                    "Производство → появление на WB, дней", min_value=0.0, max_value=90.0,
                    value=float(decision_settings.get("decision_production_wb_lead_days", 10.0) or 10.0), step=1.0,
                    key="decision_production_wb_lead_days"
                )
                purchase_target_days = st.number_input(
                    "Целевой запас закупаемого товара, дней", min_value=1.0, max_value=180.0,
                    value=float(decision_settings.get("decision_purchase_target_days", 30.0) or 30.0), step=1.0,
                    key="decision_purchase_target_days"
                )
            with logistics_cols[1]:
                default_purchase_lead_days = st.number_input(
                    "Срок поставки закупаемого товара по умолчанию, дней", min_value=0.0, max_value=180.0,
                    value=float(decision_settings.get("decision_purchase_default_lead_days", 14.0) or 14.0), step=1.0,
                    key="decision_purchase_default_lead_days"
                )
                default_purchase_moq = st.number_input(
                    "MOQ закупки по умолчанию, шт", min_value=1, max_value=100000,
                    value=int(decision_settings.get("decision_purchase_default_moq", 10) or 10), step=1,
                    key="decision_purchase_default_moq"
                )
            with logistics_cols[2]:
                ad_step_pct = st.number_input(
                    "Первый шаг снижения рекламы, %", min_value=5.0, max_value=100.0,
                    value=float(decision_settings.get("decision_ad_step_pct", 25.0) or 25.0), step=5.0,
                    key="decision_ad_step_pct"
                )
                ad_observation_days = st.number_input(
                    "Наблюдать после изменения, дней", min_value=1, max_value=30,
                    value=int(decision_settings.get("decision_ad_observation_days", 4) or 4), step=1,
                    key="decision_ad_observation_days"
                )
            if st.button("Сохранить параметры центра решений", key="save_decision_parameters"):
                updated_settings = load_settings()
                updated_settings.update({
                    "decision_margin_target": float(margin_target),
                    "decision_ad_share_limit": float(ad_share_limit),
                    "decision_return_limit": float(return_limit),
                    "decision_stock_days": float(stock_days_limit),
                    "decision_production_wb_lead_days": float(production_wb_lead_days),
                    "decision_purchase_target_days": float(purchase_target_days),
                    "decision_purchase_default_lead_days": float(default_purchase_lead_days),
                    "decision_purchase_default_moq": int(default_purchase_moq),
                    "decision_ad_step_pct": float(ad_step_pct),
                    "decision_ad_observation_days": int(ad_observation_days),
                    "decision_purchase_rules": purchase_rules,
                })
                save_settings(updated_settings)
                st.success("Параметры центра решений сохранены.")

        article_margin = build_article_margin_view(
            data.financial_products, fifo_sales, margin_target, ad_share_limit, return_limit,
            stock_days_limit, target_total_profit=fifo_profit_target,
            production_wb_lead_days=production_wb_lead_days,
            purchase_target_days=purchase_target_days,
            default_purchase_moq=int(default_purchase_moq),
            default_purchase_lead_days=default_purchase_lead_days,
            ad_step_pct=ad_step_pct,
            ad_observation_days=int(ad_observation_days),
            purchase_rules=purchase_rules,
        )
        if article_margin.empty:
            st.info("Нет данных по активным артикулам за выбранный период.")
        else:
            total_article_profit = float(article_margin["Прибыль FIFO, ₽"].sum())
            total_direct_profit = float(article_margin["Маржинальная прибыль FIFO, ₽"].sum())
            pre_reconcile_profit = float(article_margin["Прибыль до магазинной сверки, ₽"].sum())
            store_adjustment = float(article_margin["Распределено магазинной разницы, ₽"].sum())
            reconciliation_delta = total_article_profit - float(fifo_profit_target)
            total_article_revenue = float(article_margin["Выкупы по цене покупателя"].abs().sum())
            weighted_article_margin = total_article_profit / total_article_revenue * 100 if total_article_revenue else 0.0
            weighted_direct_margin = total_direct_profit / total_article_revenue * 100 if total_article_revenue else 0.0
            profitable_articles = int((article_margin["Прибыль FIFO, ₽"] > 0).sum())
            loss_articles = int((article_margin["Прибыль FIFO, ₽"] < 0).sum())
            direct_loss_articles = int((article_margin["Маржинальная прибыль FIFO, ₽"] < 0).sum())
            overhead_only_loss = int(((article_margin["Маржинальная прибыль FIFO, ₽"] >= 0) & (article_margin["Прибыль FIFO, ₽"] < 0)).sum())
            exact_events = float(article_margin["covered_events"].sum())
            all_events = float(article_margin["total_events"].sum())
            exact_coverage = exact_events / all_events * 100 if all_events else 0.0
            ad_reduction = float((-article_margin["Рекомендованное изменение рекламы, ₽"].clip(upper=0)).sum())

            margin_cards = st.columns(6)
            with margin_cards[0]:
                kpi_card("Прибыль по артикулам", money(total_article_profit), "Сверена с общей прибылью магазина")
            with margin_cards[1]:
                kpi_card("Чистая маржа", f"{weighted_article_margin:.1f}%", "После общих магазинных расходов")
            with margin_cards[2]:
                kpi_card("Прибыльных", num(profitable_articles), f"Из {num(len(article_margin))} активных артикулов")
            with margin_cards[3]:
                kpi_card("Убыточных", num(loss_articles), "Требуют проверки цены и экономики")
            with margin_cards[4]:
                kpi_card("Покрытие FIFO", f"{exact_coverage:.1f}%", f"{num(exact_events)} из {num(all_events)} событий")
            with margin_cards[5]:
                kpi_card("Сократить рекламу", money(ad_reduction), "Только реально рекомендуемое сокращение")

            economics_cards = st.columns(4)
            with economics_cards[0]:
                kpi_card("Маржинальная прибыль", money(total_direct_profit), "До общих магазинных расходов")
            with economics_cards[1]:
                kpi_card("Маржинальность", f"{weighted_direct_margin:.1f}%", "Основа решений по цене, рекламе и ассортименту")
            with economics_cards[2]:
                kpi_card("Прямо убыточных", num(direct_loss_articles), "Не покрывают собственные переменные расходы")
            with economics_cards[3]:
                kpi_card("Не покрывают общие", num(overhead_only_loss), "Прямая экономика положительная, чистая — отрицательная")

            if abs(reconciliation_delta) < 0.02:
                st.success(
                    f"Сверка прибыли выполнена: сумма по артикулам {money(total_article_profit)} "
                    f"совпадает с общей прибылью по FIFO {money(float(fifo_profit_target))}."
                )
            else:
                st.warning(
                    f"Осталось расхождение {money(reconciliation_delta)}. Не используйте рекомендации до повторной проверки."
                )
            with st.expander("Как распределена магазинная разница", expanded=False):
                reconcile_cols = st.columns(4)
                with reconcile_cols[0]:
                    kpi_card("Карточки до сверки", money(pre_reconcile_profit), "Прямые и ранее распределённые статьи")
                with reconcile_cols[1]:
                    kpi_card("Магазинная разница", money(store_adjustment), "Удержания, технические и общие строки")
                with reconcile_cols[2]:
                    kpi_card("После распределения", money(total_article_profit), "Итог по активным карточкам")
                with reconcile_cols[3]:
                    kpi_card("Контрольный итог", money(float(fifo_profit_target)), "Общая прибыль финансового блока")
                st.caption(
                    "Магазинная разница распределяется по доле выкупов покупателей. Она показана отдельной колонкой и не скрывается внутри рекламы или себестоимости."
                )

            margin_tabs = st.tabs(["Сводка", "Юнит-экономика", "Действия", "Центр решений"])
            with margin_tabs[0]:
                chart_left, chart_right = st.columns(2)
                chart_source = article_margin[article_margin["Продано нетто"] > 1].copy()
                with chart_left:
                    st.markdown("#### Прибыль по артикулам")
                    if chart_source.empty:
                        st.info("Недостаточно продаж для графика.")
                    else:
                        profit_chart = pd.concat([
                            chart_source.nlargest(7, "Прибыль FIFO, ₽"),
                            chart_source.nsmallest(7, "Прибыль FIFO, ₽"),
                        ]).drop_duplicates("Артикул WB").sort_values("Прибыль FIFO, ₽")
                        fig_margin_profit = px.bar(
                            profit_chart, x="Прибыль FIFO, ₽", y="Артикул продавца", orientation="h",
                            color="Сигнал", hover_data=["Товар", "Маржа FIFO, %", "Продано нетто"]
                        )
                        fig_margin_profit.update_layout(
                            height=430, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="₽", yaxis_title="",
                            legend_title_text="", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)"
                        )
                        st.plotly_chart(fig_margin_profit, use_container_width=True)
                with chart_right:
                    st.markdown("#### Маржа и рекламная нагрузка")
                    if chart_source.empty:
                        st.info("Недостаточно продаж для графика.")
                    else:
                        fig_margin_ads = px.scatter(
                            chart_source, x="Доля рекламы, %", y="Маржинальность FIFO, %", size="Продано нетто",
                            color="Сигнал", hover_name="Артикул продавца",
                            hover_data=["Товар", "Маржинальная прибыль FIFO, ₽", "Прибыль FIFO, ₽", "Возвраты, %", "Запас, дней"]
                        )
                        fig_margin_ads.add_hline(y=float(margin_target), line_dash="dash", annotation_text="Целевая маржинальность")
                        fig_margin_ads.add_vline(x=float(ad_share_limit), line_dash="dash", annotation_text="Лимит рекламы")
                        fig_margin_ads.update_layout(
                            height=430, margin=dict(l=10, r=10, t=10, b=10), legend_title_text="",
                            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)"
                        )
                        st.plotly_chart(fig_margin_ads, use_container_width=True)

                summary_view = article_margin[[
                    "Сигнал", "Источник", "Артикул продавца", "Товар", "Продано нетто", "Выкупы по цене покупателя",
                    "Маржинальная прибыль FIFO, ₽", "Маржинальность FIFO, %",
                    "Прибыль FIFO, ₽", "Маржа FIFO, %", "Доля рекламы, %",
                    "Возвраты, %", "Запас, дней", "Покрытие FIFO, %"
                ]].copy()
                st.dataframe(
                    summary_view, hide_index=True, use_container_width=True, height=min(580, 92 + 34 * len(summary_view)),
                    column_config={
                        "Продано нетто": st.column_config.NumberColumn(format="%.0f"),
                        "Выкупы по цене покупателя": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Маржинальная прибыль FIFO, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Маржинальность FIFO, %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Прибыль FIFO, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Маржа FIFO, %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Доля рекламы, %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Возвраты, %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Запас, дней": st.column_config.NumberColumn(format="%.1f"),
                        "Покрытие FIFO, %": st.column_config.NumberColumn(format="%.1f%%"),
                    },
                )

            with margin_tabs[1]:
                filter_cols = st.columns([1.5, 2.0, 1.0])
                with filter_cols[0]:
                    article_search = st.text_input("Поиск по артикулу или товару", key="article_margin_search")
                status_options = article_margin["Сигнал"].dropna().astype(str).drop_duplicates().tolist()
                with filter_cols[1]:
                    selected_signals = st.multiselect(
                        "Сигналы", status_options, default=status_options, key="article_margin_signals"
                    )
                with filter_cols[2]:
                    min_net_units = st.number_input(
                        "Мин. продаж нетто", min_value=0, max_value=100000, value=0, step=1, key="article_margin_min_units"
                    )
                detail_view = article_margin.copy()
                if article_search.strip():
                    needle = article_search.strip().casefold()
                    detail_view = detail_view[
                        detail_view["Артикул продавца"].astype(str).str.casefold().str.contains(needle, regex=False)
                        | detail_view["Товар"].astype(str).str.casefold().str.contains(needle, regex=False)
                    ]
                detail_view = detail_view[detail_view["Сигнал"].isin(selected_signals)]
                detail_view = detail_view[detail_view["Продано нетто"] >= int(min_net_units)]
                unit_cols = [
                    "Сигнал", "Источник", "Артикул WB", "Артикул продавца", "Товар", "Продано нетто",
                    "Цена покупателя/ед., ₽", "К перечислению/ед., ₽", "Расходы WB/ед., ₽",
                    "Реклама/ед., ₽", "FIFO COGS/ед., ₽",
                    "Маржинальная прибыль на ед., ₽", "Маржинальность FIFO, %",
                    "Общие расходы/ед., ₽", "Магазинная корректировка/ед., ₽",
                    "Чистая прибыль на ед., ₽", "Чистая маржа FIFO, %", "ROI FIFO, %",
                    "Цена прямой безубыточности, ₽", "Цена полной безубыточности, ₽",
                    "Цена для целевой маржинальности, ₽", "Цена для целевой чистой маржи, ₽",
                    "Покрытие FIFO, %", "Запас, дней"
                ]
                st.dataframe(
                    detail_view[unit_cols], hide_index=True, use_container_width=True,
                    height=min(650, 92 + 34 * max(1, len(detail_view))),
                    column_config={
                        "Продано нетто": st.column_config.NumberColumn(format="%.0f"),
                        "Цена покупателя/ед., ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "К перечислению/ед., ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Расходы WB/ед., ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Реклама/ед., ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "FIFO COGS/ед., ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Общие расходы/ед., ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Магазинная корректировка/ед., ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Маржинальная прибыль на ед., ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Чистая прибыль на ед., ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Цена прямой безубыточности, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Цена полной безубыточности, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Цена для целевой маржинальности, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Цена для целевой чистой маржи, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        "Маржинальность FIFO, %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Чистая маржа FIFO, %": st.column_config.NumberColumn(format="%.1f%%"),
                        "ROI FIFO, %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Покрытие FIFO, %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Запас, дней": st.column_config.NumberColumn(format="%.1f"),
                    },
                )
                export_margin = article_margin.drop(columns=["_priority", "_decision_priority", "nm_id"], errors="ignore")
                st.download_button(
                    "Скачать маржу по артикулам CSV",
                    export_margin.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"wb_article_margin_{start.isoformat()}_{end.isoformat()}.csv",
                    mime="text/csv",
                    key="download_article_margin_csv",
                )

            with margin_tabs[2]:
                action_view = article_margin[~article_margin["Сигнал"].isin(["Стабильно", "Мало данных"])].copy()
                if action_view.empty:
                    st.success("По заданным порогам критических действий нет.")
                else:
                    action_view["Изменение рекламы, ₽"] = action_view["Рекомендованное изменение рекламы, ₽"]
                    action_view["Рекламный резерв, ₽"] = action_view["Теоретический рекламный резерв, ₽"]
                    st.dataframe(
                        action_view[[
                            "Сигнал", "Источник", "Артикул продавца", "Товар", "Продано нетто",
                            "Маржинальная прибыль FIFO, ₽", "Маржинальность FIFO, %",
                            "Прибыль FIFO, ₽", "Маржа FIFO, %", "Цена покупателя/ед., ₽",
                            "Цена прямой безубыточности, ₽", "Цена полной безубыточности, ₽",
                            "Доля рекламы, %", "Запас, дней", "Изменение рекламы, ₽", "Рекламный резерв, ₽",
                            "Решение по закупке", "Причина", "Действие"
                        ]],
                        hide_index=True, use_container_width=True, height=min(650, 92 + 42 * len(action_view)),
                        column_config={
                            "Продано нетто": st.column_config.NumberColumn(format="%.0f"),
                            "Маржинальная прибыль FIFO, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                            "Маржинальность FIFO, %": st.column_config.NumberColumn(format="%.1f%%"),
                            "Прибыль FIFO, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                            "Маржа FIFO, %": st.column_config.NumberColumn(format="%.1f%%"),
                            "Цена покупателя/ед., ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                            "Цена прямой безубыточности, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                            "Цена полной безубыточности, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                            "Доля рекламы, %": st.column_config.NumberColumn(format="%.1f%%"),
                            "Запас, дней": st.column_config.NumberColumn(format="%.1f"),
                            "Изменение рекламы, ₽": st.column_config.NumberColumn(
                                "Рекомендовано по рекламе, ₽", format="%.2f ₽",
                                help="Фактическая рекомендация. При дефиците товара положительное значение всегда блокируется."
                            ),
                            "Рекламный резерв, ₽": st.column_config.NumberColumn(
                                format="%.2f ₽",
                                help="Теоретический предел по марже. Это не рекомендация к увеличению рекламы."
                            ),
                        },
                    )

            with margin_tabs[3]:
                decision_view = article_margin.copy()
                decision_filters = st.columns([1.2, 1.2, 2.0])
                priority_options = ["Критический", "Высокий", "Средний", "Низкий"]
                focus_options = decision_view["Фокус решения"].dropna().astype(str).drop_duplicates().tolist()
                with decision_filters[0]:
                    selected_priorities = st.multiselect(
                        "Приоритет", priority_options, default=["Критический", "Высокий", "Средний"],
                        key="decision_center_priorities"
                    )
                with decision_filters[1]:
                    selected_focus = st.multiselect(
                        "Фокус", focus_options, default=focus_options, key="decision_center_focus"
                    )
                with decision_filters[2]:
                    decision_search = st.text_input(
                        "Поиск по артикулу или товару", key="decision_center_search"
                    )
                if selected_priorities:
                    decision_view = decision_view[decision_view["Приоритет решения"].isin(selected_priorities)]
                else:
                    decision_view = decision_view.iloc[0:0]
                if selected_focus:
                    decision_view = decision_view[decision_view["Фокус решения"].isin(selected_focus)]
                else:
                    decision_view = decision_view.iloc[0:0]
                if decision_search.strip():
                    needle = decision_search.strip().casefold()
                    decision_view = decision_view[
                        decision_view["Артикул продавца"].astype(str).str.casefold().str.contains(needle, regex=False)
                        | decision_view["Товар"].astype(str).str.casefold().str.contains(needle, regex=False)
                    ]
                decision_view = decision_view.sort_values(
                    ["_decision_priority", "Ожидаемый эффект, ₽"], ascending=[True, False]
                )

                urgent = int(article_margin["Приоритет решения"].isin(["Критический", "Высокий"]).sum())
                direct_losses = int((article_margin["Маржинальная прибыль FIFO, ₽"] < 0).sum())
                full_only_losses = int(((article_margin["Маржинальная прибыль FIFO, ₽"] >= 0) & (article_margin["Прибыль FIFO, ₽"] < 0)).sum())
                visible_effect = float(decision_view["Ожидаемый эффект, ₽"].clip(lower=0).sum()) if not decision_view.empty else 0.0
                center_cards = st.columns(4)
                with center_cards[0]: kpi_card("Срочных решений", num(urgent), "Критический и высокий приоритет")
                with center_cards[1]: kpi_card("Прямо убыточных", num(direct_losses), "Сначала исправить экономику карточки")
                with center_cards[2]: kpi_card("Только общие расходы", num(full_only_losses), "Карточки не следует отключать автоматически")
                with center_cards[3]: kpi_card("Оценочный эффект", money(visible_effect), "Сумма независимых оценок по выбранному фильтру")

                if decision_view.empty:
                    st.success("По выбранным фильтрам действий нет.")
                else:
                    st.dataframe(
                        decision_view[[
                            "Приоритет решения", "Фокус решения", "Источник", "Артикул продавца", "Товар",
                            "Решение сейчас", "Ожидаемый эффект, ₽", "Основание эффекта",
                            "Маржинальная прибыль FIFO, ₽", "Маржинальность FIFO, %",
                            "Прибыль FIFO, ₽", "Маржа FIFO, %", "Запас, дней",
                            "Поставщик закупки", "MOQ закупки, шт", "Срок поставки закупки, дней", "Целевой запас закупки, дней",
                            "Цена закупки, ₽/шт", "Цена прямой безубыточности, ₽", "Цена полной безубыточности, ₽"
                        ]],
                        hide_index=True, use_container_width=True, height=min(720, 110 + 46 * len(decision_view)),
                        column_config={
                            "Ожидаемый эффект, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                            "Маржинальная прибыль FIFO, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                            "Маржинальность FIFO, %": st.column_config.NumberColumn(format="%.1f%%"),
                            "Прибыль FIFO, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                            "Маржа FIFO, %": st.column_config.NumberColumn(format="%.1f%%"),
                            "Запас, дней": st.column_config.NumberColumn(format="%.1f"),
                            "MOQ закупки, шт": st.column_config.NumberColumn(format="%d"),
                            "Срок поставки закупки, дней": st.column_config.NumberColumn(format="%.0f"),
                            "Целевой запас закупки, дней": st.column_config.NumberColumn(format="%.0f"),
                            "Цена закупки, ₽/шт": st.column_config.NumberColumn(format="%.2f ₽"),
                            "Цена прямой безубыточности, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                            "Цена полной безубыточности, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                        },
                    )
                    center_export = decision_view.drop(columns=["_priority", "_decision_priority", "nm_id"], errors="ignore")
                    st.download_button(
                        "Скачать центр решений CSV",
                        center_export.to_csv(index=False).encode("utf-8-sig"),
                        file_name=f"wb_decision_center_{start.isoformat()}_{end.isoformat()}.csv",
                        mime="text/csv", key="download_decision_center_csv"
                    )

                purchased_rules_view = article_margin[article_margin["Источник"].eq("Закупаемый товар")].copy()
                with st.expander("MOQ и сроки закупаемых товаров", expanded=False):
                    st.caption(
                        "Количество заказа округляется вверх до кратного MOQ. Горизонт заказа — максимум из целевого запаса "
                        "и суммы срока поставки со страховым запасом. Значения сохраняются локально для каждого артикула."
                    )
                    if purchased_rules_view.empty:
                        st.info("Активных закупаемых товаров нет.")
                    else:
                        supplier_options = [""]
                        try:
                            supplier_directory_for_rules = read_suppliers(active_only=True)
                            if not supplier_directory_for_rules.empty:
                                supplier_options += sorted(set(supplier_directory_for_rules["name"].fillna("").astype(str).str.strip()) - {""})
                        except Exception:
                            supplier_options = [""]
                        existing_rule_suppliers = set(purchased_rules_view.get("Поставщик закупки", pd.Series(dtype=str)).fillna("").astype(str).str.strip()) - {""}
                        supplier_options = [""] + sorted((set(supplier_options) - {""}) | existing_rule_suppliers)
                        rules_editor = purchased_rules_view[[
                            "Артикул WB", "Артикул продавца", "Товар", "Поставщик закупки",
                            "MOQ закупки, шт", "Срок поставки закупки, дней", "Целевой запас закупки, дней",
                            "Цена закупки, ₽/шт"
                        ]].drop_duplicates(subset=["Артикул WB"]).sort_values("Артикул продавца")
                        edited_purchase_rules = st.data_editor(
                            rules_editor, hide_index=True, use_container_width=True,
                            key="purchase_rules_editor",
                            column_config={
                                "Артикул WB": st.column_config.NumberColumn(disabled=True, format="%d"),
                                "Артикул продавца": st.column_config.TextColumn(disabled=True),
                                "Товар": st.column_config.TextColumn(disabled=True),
                                "Поставщик закупки": st.column_config.SelectboxColumn(options=supplier_options, required=False),
                                "MOQ закупки, шт": st.column_config.NumberColumn(min_value=1, step=1, format="%d"),
                                "Срок поставки закупки, дней": st.column_config.NumberColumn(min_value=0.0, step=1.0, format="%.0f"),
                                "Целевой запас закупки, дней": st.column_config.NumberColumn(min_value=1.0, step=1.0, format="%.0f"),
                                "Цена закупки, ₽/шт": st.column_config.NumberColumn(min_value=0.0, step=1.0, format="%.2f"),
                            },
                        )
                        if st.button("Сохранить MOQ и сроки", key="save_purchase_rules"):
                            rules_map = {}
                            for _, rule_row in edited_purchase_rules.iterrows():
                                nm_id = int(rule_row.get("Артикул WB", 0) or 0)
                                if nm_id <= 0:
                                    continue
                                rules_map[str(nm_id)] = {
                                    "supplier_name": str(rule_row.get("Поставщик закупки", "") or "").strip(),
                                    "moq": max(1, int(float(rule_row.get("MOQ закупки, шт", default_purchase_moq) or default_purchase_moq))),
                                    "lead_days": max(0.0, float(rule_row.get("Срок поставки закупки, дней", default_purchase_lead_days) or default_purchase_lead_days)),
                                    "target_days": max(1.0, float(rule_row.get("Целевой запас закупки, дней", purchase_target_days) or purchase_target_days)),
                                    "unit_cost_rub": max(0.0, float(rule_row.get("Цена закупки, ₽/шт", 0) or 0)),
                                }
                            updated_settings = load_settings()
                            updated_settings["decision_purchase_rules"] = rules_map
                            save_settings(updated_settings)
                            st.success("MOQ и сроки закупаемых товаров сохранены.")
                            st.rerun()
                st.caption(
                    "Ожидаемый эффект — управленческая оценка, а не гарантированный прогноз. Для цены предполагаются прежние объём продаж "
                    "и коэффициент перечисления WB; для масштабирования — рост продаж на 10% при неизменной юнит-экономике. "
                    "Эффекты разных действий могут пересекаться, поэтому их сумму нельзя автоматически считать прогнозом прибыли."
                )

            st.caption(
                "При покрытии FIFO ниже 100% точная стоимость применяется только к новым обработанным продажам, "
                "а оставшаяся часть оценивается по сохранённой базовой себестоимости. Операционные решения используют маржинальную прибыль, "
                "а чистая прибыль после общих расходов служит для контроля результата магазина. Пополнение учитывает срок до появления товара на WB, "
                "страховой запас и MOQ; рекламные изменения выполняются первым ограниченным шагом с последующей переоценкой. "
                "Расчётные цены и действия не применяются автоматически."
            )

        rows = pd.DataFrame([
            ["Разница: выкупы − к перечислению (справочно)", -f["sales_to_pay_gap"]],
            ["Эквайринг (в составе разницы, справочно)", -f["acquiring"]],
            ["Логистика", -f["logistics"]],
            ["Дополнительная логистика", -f["rebill_logistics"]],
            ["Хранение", -f["storage"]],
            ["Штрафы", -f["penalties"]],
            ["Удержания", -f["deductions"]],
            ["Приёмка", -f["acceptance"]],
            ["Доплаты WB", f["additional_payment"]],
            ["Реклама", -float(f.get("financial_ad_spend", data.kpi["ad_spend"]) or 0)],
            ["Себестоимость", -f["cost"]],
        ], columns=["Статья", "Влияние на результат, ₽"])
        st.dataframe(
            rows,
            hide_index=True,
            use_container_width=True,
            column_config={"Влияние на результат, ₽": st.column_config.NumberColumn(format="%.2f ₽")},
        )

        all_fin_products = data.financial_products.copy()
        article_fin = all_fin_products.copy()

        st.markdown("### Распределение общих расходов")
        allocation_cols = st.columns(5)
        with allocation_cols[0]:
            kpi_card("Распределено по товарам", money(f.get("common_expense_pool", 0)), f"Пропорционально {f.get('allocation_basis', 'выручке')}")
        with allocation_cols[1]:
            kpi_card("Удержания магазина", money(f.get("unallocated_deductions", 0)), "Не распределяются по товарам")
        with allocation_cols[2]:
            kpi_card("Вне активного каталога", money(f.get("technical_impact", 0)), "Отдельно от рейтинга")
        with allocation_cols[3]:
            kpi_card("Общая реклама без артикула", money(f.get("unallocated_ad_spend", 0)), "Включена в распределение")
        with allocation_cols[4]:
            kpi_card("Остаток на уровне магазина", money(f.get("remaining_store_impact", 0)), "После распределения")
        st.caption(
            "Хранение, дополнительная логистика и другие общие расходы без артикула распределяются по активным товарам "
            f"пропорционально {f.get('allocation_basis', 'выручке')}. Удержания и технические строки остаются на уровне магазина."
        )

        st.markdown("### Рейтинг товаров")
        if article_fin.empty:
            st.info("Нет строк финансового отчёта по активным артикулам за выбранный период.")
        else:
            eligible_fin = article_fin[article_fin["Статус"] != "Недостаточно данных"].copy()
            rank_tabs = st.tabs(["По общей прибыли", "По прибыли на единицу"])
            with rank_tabs[0]:
                rc1, rc2 = st.columns(2)
                if eligible_fin.empty:
                    st.info("Недостаточно продаж для построения рейтинга.")
                else:
                    top = eligible_fin.nlargest(7, "Расчётная прибыль").sort_values("Расчётная прибыль")
                    bottom = eligible_fin.nsmallest(7, "Расчётная прибыль").sort_values("Расчётная прибыль")
                    with rc1:
                        st.markdown("#### Лидеры по общей прибыли")
                        fig_top = px.bar(top, x="Расчётная прибыль", y="Артикул продавца", orientation="h", text_auto=".2s")
                        fig_top.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="₽", yaxis_title="", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                        st.plotly_chart(fig_top, use_container_width=True)
                    with rc2:
                        st.markdown("#### Нижняя группа по общей прибыли")
                        fig_bottom = px.bar(bottom, x="Расчётная прибыль", y="Артикул продавца", orientation="h", text_auto=".2s")
                        fig_bottom.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="₽", yaxis_title="", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                        st.plotly_chart(fig_bottom, use_container_width=True)
            with rank_tabs[1]:
                rc1, rc2 = st.columns(2)
                if eligible_fin.empty:
                    st.info("Недостаточно продаж для построения рейтинга.")
                else:
                    top_unit = eligible_fin.nlargest(7, "Расчётная прибыль на ед.").sort_values("Расчётная прибыль на ед.")
                    bottom_unit = eligible_fin.nsmallest(7, "Расчётная прибыль на ед.").sort_values("Расчётная прибыль на ед.")
                    with rc1:
                        st.markdown("#### Лидеры по прибыли на единицу")
                        fig_top_unit = px.bar(top_unit, x="Расчётная прибыль на ед.", y="Артикул продавца", orientation="h", text_auto=".2s")
                        fig_top_unit.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="₽/шт", yaxis_title="", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                        st.plotly_chart(fig_top_unit, use_container_width=True)
                    with rc2:
                        st.markdown("#### Нижняя группа по прибыли на единицу")
                        fig_bottom_unit = px.bar(bottom_unit, x="Расчётная прибыль на ед.", y="Артикул продавца", orientation="h", text_auto=".2s")
                        fig_bottom_unit.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="₽/шт", yaxis_title="", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                        st.plotly_chart(fig_bottom_unit, use_container_width=True)

            leaders = int((article_fin["Статус"] == "Лидер").sum())
            profitable = int((article_fin["Статус"] == "Прибыльный").sum())
            low_margin = int((article_fin["Статус"] == "Низкая маржа").sum())
            loss_making = int((article_fin["Статус"] == "Убыточный").sum())
            insufficient = int((article_fin["Статус"] == "Недостаточно данных").sum())
            status_cols = st.columns(5)
            with status_cols[0]: kpi_card("Лидеры", num(leaders), "Верхний квартиль")
            with status_cols[1]: kpi_card("Прибыльные", num(profitable), "Маржа от 10%")
            with status_cols[2]: kpi_card("Низкая маржа", num(low_margin), "Маржа ниже 10%")
            with status_cols[3]: kpi_card("Убыточные", num(loss_making), "После общих расходов")
            with status_cols[4]: kpi_card("Мало данных", num(insufficient), "0–1 продажа")

            st.markdown("### Что масштабировать / что проверить")
            scale = article_fin[
                article_fin["Статус"].isin(["Лидер", "Прибыльный"])
                & (article_fin["Расчётная маржа, %"] >= 15)
                & (article_fin["Возвраты, %"] < 15)
            ].nlargest(7, "Расчётная прибыль")
            review = article_fin[article_fin["Статус"].isin(["Убыточный", "Низкая маржа"])].nsmallest(7, "Расчётная прибыль")
            action_cols = st.columns(2)
            compact_action_cols = [
                "Артикул продавца", "Товар", "Продано нетто", "Расчётная прибыль",
                "Расчётная маржа, %", "Остаток", "Запас, дней", "Рекомендация"
            ]
            action_config = {
                "Расчётная прибыль": st.column_config.NumberColumn(format="%.0f ₽"),
                "Расчётная маржа, %": st.column_config.NumberColumn(format="%.1f%%"),
                "Остаток": st.column_config.NumberColumn(format="%.0f"),
                "Запас, дней": st.column_config.NumberColumn(format="%.1f"),
            }
            with action_cols[0]:
                st.markdown("#### Кандидаты на масштабирование")
                if scale.empty:
                    st.info("Пока нет товаров, одновременно проходящих фильтр прибыли, маржи и возвратов.")
                else:
                    st.dataframe(
                        scale[compact_action_cols], hide_index=True, use_container_width=True, height=330,
                        column_config=action_config,
                    )
            with action_cols[1]:
                st.markdown("#### Проверить цену, рекламу или отключение")
                if review.empty:
                    st.success("Убыточных и низкомаржинальных товаров за период нет.")
                else:
                    st.dataframe(
                        review[compact_action_cols], hide_index=True, use_container_width=True, height=330,
                        column_config=action_config,
                    )
            if insufficient:
                st.caption(f"Ещё {insufficient} товар(ов) имеют 0–1 продажу и не участвуют в выводах об убыточности.")

        st.markdown("### Юнит-экономика по артикулам")
        if article_fin.empty:
            st.info("Нет данных по активным артикулам за выбранный период.")
        else:
            filter_cols = st.columns([1, 1.2, 1.4])
            categories = sorted([str(v) for v in article_fin["Категория"].dropna().unique() if str(v).strip()])
            statuses = ["Лидер", "Прибыльный", "Низкая маржа", "Убыточный", "Недостаточно данных"]
            with filter_cols[0]:
                selected_category = st.selectbox("Категория", ["Все"] + categories)
            with filter_cols[1]:
                selected_statuses = st.multiselect("Статус", statuses, default=statuses)
            with filter_cols[2]:
                article_search = st.text_input("Поиск по артикулу или названию", placeholder="Например: beige или 391054484")

            filtered_fin = article_fin.copy()
            if selected_category != "Все":
                filtered_fin = filtered_fin[filtered_fin["Категория"].astype(str) == selected_category]
            if selected_statuses:
                filtered_fin = filtered_fin[filtered_fin["Статус"].isin(selected_statuses)]
            else:
                filtered_fin = filtered_fin.iloc[0:0]
            if article_search.strip():
                needle = article_search.strip().casefold()
                search_text = (
                    filtered_fin["Артикул WB"].astype(str) + " "
                    + filtered_fin["Артикул продавца"].astype(str) + " "
                    + filtered_fin["Товар"].astype(str)
                ).str.casefold()
                filtered_fin = filtered_fin[search_text.str.contains(needle, regex=False)]

            compact_cols = [
                "Ранг", "Статус", "Артикул продавца", "Товар", "Продано нетто",
                "Расчётная прибыль", "Расчётная прибыль на ед.", "Расчётная маржа, %",
                "Остаток", "Продаж/день", "Запас, дней",
                "Доля рекламы, %", "Возвраты, %", "Основная причина", "Рекомендация"
            ]
            st.dataframe(
                filtered_fin[compact_cols],
                hide_index=True,
                use_container_width=True,
                height=560,
                column_config={
                    "Расчётная прибыль": st.column_config.NumberColumn(format="%.2f ₽"),
                    "Расчётная прибыль на ед.": st.column_config.NumberColumn(format="%.2f ₽"),
                    "Расчётная маржа, %": st.column_config.NumberColumn(format="%.1f%%"),
                    "Остаток": st.column_config.NumberColumn(format="%.0f"),
                    "Продаж/день": st.column_config.NumberColumn(format="%.2f"),
                    "Запас, дней": st.column_config.NumberColumn(format="%.1f"),
                    "Доля рекламы, %": st.column_config.NumberColumn(format="%.1f%%"),
                    "Возвраты, %": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )
            with st.expander("Полная детализация по товарам", expanded=False):
                st.dataframe(
                    filtered_fin, hide_index=True, use_container_width=True, height=620,
                    column_config={
                        c: st.column_config.NumberColumn(format="%.2f ₽")
                        for c in [
                            "Выкупы по цене покупателя", "Продажи по отчёту", "К перечислению за товар",
                            "Прямые расходы WB", "Реклама", "Себестоимость ед.", "Себестоимость",
                            "Маржинальная прибыль", "Маржинальная прибыль на ед.",
                            "Распределено общих расходов", "Расчётная прибыль", "Расчётная прибыль на ед."
                        ]
                    } | {
                        c: st.column_config.NumberColumn(format="%.1f%%")
                        for c in [
                            "Возвраты, %", "Доля рекламы, %", "Доля расходов WB, %",
                            "Доля себестоимости, %", "Маржинальность, %", "Доля общих расходов, %",
                            "Расчётная маржа, %", "Рентабельность затрат, %", "Доля прибыли, %"
                        ]
                    },
                )

        if not data.financial_unallocated.empty:
            with st.expander("Операции уровня магазина и товары вне активного каталога", expanded=False):
                st.caption(
                    "Эти строки учтены в общей прибыли магазина. Общие расходы без артикула распределены по активным товарам, "
                    "а удержания и товары вне активного каталога сохранены отдельно для контроля."
                )
                st.dataframe(
                    data.financial_unallocated, hide_index=True, use_container_width=True,
                    column_config={
                        c: st.column_config.NumberColumn(format="%.2f ₽")
                        for c in ["Логистика", "Доп. логистика", "Хранение", "Штрафы", "Удержания", "Приёмка", "Доплаты WB", "Реклама", "Влияние на прибыль"]
                    },
                )

        st.markdown("### Сверка с отчётом WB")
        st.caption("Загрузите Excel из раздела «Аналитика → Доходы и расходы». Файл нужен только для контрольной сверки и никуда не отправляется.")
        uploaded_report = st.file_uploader("Отчёт «Доходы и расходы» (.xlsx)", type=["xlsx"], key="income_expense_report")
        reconciliation_df = pd.DataFrame()
        wb_detail = pd.DataFrame()
        wb_metrics = None
        if uploaded_report is not None:
            try:
                wb_metrics, wb_detail = parse_income_expense_report(uploaded_report)
                reconciliation_df = build_reconciliation(f, wb_metrics)
                wb_start = wb_metrics.get("period_start")
                wb_end = wb_metrics.get("period_end")
                if wb_start and wb_end and (wb_start != start or wb_end != end):
                    st.warning(f"Период файла {wb_start:%d.%m.%Y}–{wb_end:%d.%m.%Y} не совпадает с выбранным периодом дашборда {start:%d.%m.%Y}–{end:%d.%m.%Y}.")
                else:
                    st.success("Период файла совпадает с выбранным периодом дашборда.")

                wb_cards = st.columns(4)
                with wb_cards[0]: kpi_card("Итог по товарам WB", money(wb_metrics["product_result"]), "Из загруженного Excel")
                with wb_cards[1]: kpi_card("Комиссия WB", money(wb_metrics["commission"]), "Контрольная величина")
                with wb_cards[2]: kpi_card("Эквайринг WB", money(wb_metrics["acquiring"]), "Контрольная величина")
                with wb_cards[3]: kpi_card("Логистика WB", money(wb_metrics["logistics"]), "Контрольная величина")

                st.dataframe(
                    reconciliation_df,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Дашборд": st.column_config.NumberColumn(format="%.2f"),
                        "Отчёт WB": st.column_config.NumberColumn(format="%.2f"),
                        "Отклонение": st.column_config.NumberColumn(format="%.2f"),
                        "Отклонение, %": st.column_config.NumberColumn(format="%.2f%%"),
                    },
                )
                deviations = int((reconciliation_df["Статус"] != "Совпадает").sum())
                if deviations:
                    st.warning(f"Есть отклонения по {deviations} показателям. Они выделены для дальнейшей проверки источников и состава операций.")
                else:
                    st.success("Все контрольные показатели совпадают в пределах допуска.")
            except Exception as exc:
                st.error(f"Не удалось прочитать отчёт WB: {exc}")

        st.markdown("### Выгрузка")
        csv_data = all_fin_products.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig") if not all_fin_products.empty else b""
        try:
            excel_data = build_finance_excel(
                start=start,
                end=end,
                financial=f,
                ad_spend=float(f.get("financial_ad_spend", data.kpi["ad_spend"]) or 0),
                financial_products=all_fin_products,
                expense_rows=rows,
                reconciliation=reconciliation_df,
                wb_detail=wb_detail,
                store_operations=data.financial_unallocated,
            )
        except Exception as exc:
            excel_data = b""
            st.warning(f"Excel-выгрузка временно недоступна: {exc}")
        dl1, dl2 = st.columns(2)
        with dl1:
            st.download_button(
                "Скачать юнит-экономику Excel",
                data=excel_data,
                file_name=f"wb_unit_economics_{start}_{end}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                disabled=not bool(excel_data),
            )
        with dl2:
            st.download_button(
                "Скачать экономику CSV",
                data=csv_data,
                file_name=f"wb_finance_by_product_{start}_{end}.csv",
                mime="text/csv",
                use_container_width=True,
                disabled=not bool(csv_data),
            )

        st.info(
            "Показатель «Выкупы по цене покупателя» и показатель «Продажа по фин. отчётам» имеют разные базы расчёта WB. "
            "Разница между выкупами и суммой к перечислению включает комиссию, эквайринг и корректировки; "
            "она показана только для сверки и повторно из прибыли не вычитается. Общая прибыль магазина: "
            "к перечислению + доплаты − расходы WB − реклама − себестоимость. На уровне товара отдельно показана "
            "маржинальная прибыль и расчётная прибыль после распределения общих расходов. Удержания магазина по товарам не распределяются."
        )
        if f["cost"] <= 0 or f["cost_coverage"] < 99.9:
            st.warning(
                "Себестоимость заполнена не по всем проданным товарам. Пока показатель прибыли завышен. "
                "Заполните себестоимость комплектов в разделе «Настройки»."
            )
        st.warning("Финансовый отчёт WB формируется с задержкой. Текущая неделя может быть неполной до закрытия отчётного периода.")

