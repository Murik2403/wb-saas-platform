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
    NO_PACKAGE_ROLL_LENGTH, is_packaged_material, packages_to_buy,
)
from backup_tools import (
    backup_bytes,
    create_backup,
    inspect_backup,
    list_backups,
    restore_backup,
)
from config import (
    delete_ozon_credentials,
    delete_token,
    get_ozon_credentials,
    get_token,
    save_ozon_credentials,
    save_settings,
    save_token,
)
from db import (
    apply_forecast_costs,
    clear_demo_data,
    fifo_reconciliation_status,
    get_fifo_opening_rate,
    get_production_capacity,
    initialize_finished_goods_fifo,
    initialize_material_fifo,
    initialize_sales_fifo_tracking,
    process_sales_fifo_events,
    read_finished_goods_cost_layers,
    read_finished_goods_fifo_summary,
    read_material_cost_layers,
    read_material_cost_rates,
    read_material_fifo_summary,
    read_sales_fifo_events,
    read_table,
    retry_sales_fifo_errors,
    sales_fifo_tracking_status,
    save_auto_cost_settings,
    save_costs,
    save_material_inventory,
    save_product_pipeline,
    save_production_capacity,
    save_production_settings,
    set_fifo_opening_rate,
)
from demo_data import (
    generate_demo,
)
from pathlib import (
    Path,
)
from sync import (
    sync_all,
    sync_finances,
)
from wb_api import (
    WBAPI,
)
from agents.marketplaces.ozon_client import OzonAgentClient


def render(ctx: dict) -> None:
    settings = ctx['settings']

    st.markdown('<div class="wb-title">Настройки</div>', unsafe_allow_html=True)
    st.markdown('<div class="wb-subtitle">Подключение кабинета, обновление данных и себестоимость</div>', unsafe_allow_html=True)

    # 8 top-level sections as tabs instead of one long scroll -- pure layout
    # change, every section's own logic/order is untouched. Streamlit runs
    # every tab's code on each script rerun regardless of which tab is
    # visually active, in the order the `with tabs[i]:` blocks appear below,
    # so cross-section variables computed early (catalog, current_costs from
    # "Себестоимость") stay available to later tabs exactly as before.
    tabs = st.tabs([
        "Подключение", "Обновление данных", "Себестоимость", "Производство",
        "Остатки сырья", "Готовая продукция", "Мощность", "Резервные копии",
    ])

    with tabs[0]:
        st.markdown("### Токен WB API")
        st.caption("Токен сохраняется локально на этом компьютере. В облако он не отправляется.")
        token_input = st.text_input("Вставьте персональный токен", type="password", placeholder="eyJhbGciOi...")
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            if st.button("Сохранить токен", type="primary", use_container_width=True):
                try:
                    save_token(token_input)
                    st.success("Токен сохранён")
                except Exception as exc:
                    st.error(str(exc))
        with c2:
            if st.button("Проверить подключение", use_container_width=True):
                token = token_input.strip() or get_token()
                if not token:
                    st.error("Сначала вставьте токен")
                else:
                    try:
                        with st.spinner("Проверяю доступ к статистике..."):
                            ok = WBAPI(token).test_statistics()
                        st.success("Подключение работает" if ok else "Ответ получен, но формат отличается")
                    except Exception as exc:
                        st.error(str(exc))
        with c3:
            if st.button("Удалить сохранённый токен"):
                delete_token()
                st.success("Токен удалён")

        st.divider()
        st.markdown("### Ключи Ozon Seller API")
        st.caption(
            "Для агента цен Ozon. Возьмите в кабинете Ozon → Настройки → Seller API. "
            "Реклама Ozon требует отдельные ключи Performance API — их сюда вставлять не нужно."
        )
        ozon_client_id_input = st.text_input("Client-Id", placeholder="123456")
        ozon_api_key_input = st.text_input("Api-Key", type="password", placeholder="xxxxxxxx-xxxx-...")
        oc1, oc2, oc3 = st.columns([1, 1, 2])
        with oc1:
            if st.button("Сохранить ключи Ozon", type="primary", use_container_width=True):
                try:
                    save_ozon_credentials(ozon_client_id_input, ozon_api_key_input)
                    st.success("Ключи Ozon сохранены")
                except Exception as exc:
                    st.error(str(exc))
        with oc2:
            if st.button("Проверить подключение", key="ozon_check_connection", use_container_width=True):
                creds = get_ozon_credentials()
                client_id = ozon_client_id_input.strip() or (creds[0] if creds else "")
                api_key = ozon_api_key_input.strip() or (creds[1] if creds else "")
                if not client_id or not api_key:
                    st.error("Сначала укажите Client-Id и Api-Key")
                else:
                    try:
                        with st.spinner("Проверяю доступ к ценам Ozon..."):
                            rows = OzonAgentClient(client_id, api_key).get_prices()
                        st.success(f"Подключение работает, товаров: {len(rows)}")
                    except Exception as exc:
                        st.error(str(exc))
        with oc3:
            if st.button("Удалить сохранённые ключи Ozon"):
                delete_ozon_credentials()
                st.success("Ключи Ozon удалены")

    with tabs[1]:
        st.markdown("### Обновление данных")
        # Minimum is 15 min on purpose: WB's statistics API rate-limits per
        # method (~1 request/minute) AND only refreshes its own data roughly
        # every 30 minutes, so polling more often gives no fresher numbers --
        # it just burns the rate-limit budget and risks throttling. 30 is the
        # sweet spot; the floor of 15 leaves headroom without being reckless.
        sync_minutes = st.number_input(
            "Интервал автоматического обновления, минут", min_value=15, max_value=1440,
            value=int(settings.get("sync_interval_minutes", 30)), step=15,
            help="Статистика WB сама обновляется примерно раз в 30 минут, поэтому опрашивать чаще смысла нет — "
                 "свежее не станет, а частые запросы упираются в лимиты WB. Рекомендуется 30 минут.",
        )
        st.caption(
            "⚠️ Чаще чем раз в 15 минут WB не даёт опрашивать не случайно: у статистического API строгие лимиты "
            "запросов, а данные всё равно обновляются на стороне WB лишь раз в ~30 минут. Оптимально — 30 минут."
        )
        history_days = st.number_input("История заказов и продаж при первом запуске, дней", min_value=7, max_value=90, value=int(settings.get("initial_history_days", 90)))
        agent_minutes = st.number_input("Интервал обновления рекомендаций агентов, минут", min_value=15, max_value=1440, value=int(settings.get("agent_interval_minutes", 60)), step=15, help="Агенты только предлагают изменения в фоне — применяет их человек на странице «Агенты».")
        if st.button("Сохранить параметры"):
            settings["sync_interval_minutes"] = int(sync_minutes)
            settings["initial_history_days"] = int(history_days)
            settings["agent_interval_minutes"] = int(agent_minutes)
            save_settings(settings)
            st.success("Параметры сохранены")

        if st.button("Синхронизировать сейчас", type="primary"):
            try:
                with st.spinner("Загружаю заказы, продажи, финансы, остатки и рекламу..."):
                    result = sync_all()
                st.success(f"Готово: {result}")
                st.cache_data.clear()
            except Exception as exc:
                st.error(str(exc))

        finance_days = st.number_input("Период финансового отчёта, дней", min_value=7, max_value=365, value=90, step=7)
        if st.button("Загрузить финансовый отчёт"):
            try:
                with st.spinner("Загружаю детализацию реализации WB. Запрос может занять до минуты..."):
                    result = sync_finances(int(finance_days))
                st.success(f"Финансовый отчёт загружен: {result}")
                st.cache_data.clear()
            except Exception as exc:
                st.error(str(exc))

    with tabs[2]:
        st.markdown("### Себестоимость")
        st.caption("Указывайте себестоимость одной продаваемой единицы WB. Если карточка продаёт комплект из нескольких изделий — укажите себестоимость всего комплекта.")
        current_costs = read_table("costs")
        catalog = read_table("products_catalog")

        if not catalog.empty:
            st.caption(f"Активных карточек в каталоге WB: {len(catalog)}. Список сформирован напрямую из кабинета, поэтому тестовые и чужие артикулы сюда не попадают.")
            base = catalog[["nm_id", "supplier_article", "product_name"]].copy()
            if not current_costs.empty:
                base = base.merge(
                    current_costs[["nm_id", "cost_per_wb_unit", "note"]],
                    on="nm_id",
                    how="left",
                )
        else:
            st.warning("Каталог товаров ещё не загружен. Нажмите «Синхронизировать сейчас». До этого используется резервный список из заказов, продаж и остатков.")
            product_ids = set()
            article_map = {}
            for table in ("orders", "sales", "stocks"):
                frame = read_table(table)
                if not frame.empty and "nm_id" in frame.columns:
                    ids = pd.to_numeric(frame["nm_id"], errors="coerce").dropna().astype(int)
                    product_ids.update(v for v in ids.tolist() if v > 0)
                    if "supplier_article" in frame.columns:
                        for _, row in frame.iterrows():
                            try:
                                article_map[int(row.get("nm_id") or 0)] = str(row.get("supplier_article") or "")
                            except (TypeError, ValueError):
                                pass
            base = pd.DataFrame({"nm_id": sorted(product_ids)})
            base["supplier_article"] = base["nm_id"].map(article_map).fillna("") if not base.empty else ""
            base["product_name"] = ""
            if not current_costs.empty:
                base = base.merge(current_costs[["nm_id", "cost_per_wb_unit", "note"]], on="nm_id", how="left")

        if base.empty:
            base = pd.DataFrame(columns=["nm_id", "supplier_article", "product_name", "cost_per_wb_unit", "note"])
        for col in ["supplier_article", "product_name", "cost_per_wb_unit", "note"]:
            if col not in base.columns:
                base[col] = "" if col != "cost_per_wb_unit" else 0.0
        base["cost_per_wb_unit"] = pd.to_numeric(base["cost_per_wb_unit"], errors="coerce").fillna(0.0)
        base["note"] = base["note"].fillna("")

        edited = st.data_editor(
            base[["nm_id", "supplier_article", "product_name", "cost_per_wb_unit", "note"]],
            num_rows="fixed" if not catalog.empty else "dynamic",
            hide_index=True,
            use_container_width=True,
            disabled=["nm_id", "supplier_article", "product_name"] if not catalog.empty else [],
            column_config={
                "nm_id": st.column_config.NumberColumn("Артикул WB", required=True, format="%d"),
                "supplier_article": "Артикул продавца",
                "product_name": "Товар",
                "cost_per_wb_unit": st.column_config.NumberColumn("Себестоимость единицы", min_value=0.0, format="%.2f ₽"),
                "note": "Примечание",
            },
        )
        if st.button("Сохранить себестоимость"):
            try:
                save_costs(edited[["nm_id", "supplier_article", "cost_per_wb_unit", "note"]])
                st.success("Себестоимость сохранена")
                st.cache_data.clear()
            except Exception as exc:
                st.error(str(exc))

        st.divider()
        st.markdown("#### Текущая себестоимость и прогноз новой партии")
        st.info(
            "В прибыли используется текущая фиксированная себестоимость, указанная выше по каждому товару. "
            "Цена новой закупки показывается отдельно как прогноз и не меняет прибыль, пока вы явно не примените "
            "её к выбранным товарам."
        )
        st.caption(
            "Прогноз новой партии учитывает полную стоимость закупки сырья: цену поставщика, доставку, курс и прочие расходы. "
            "Стоимость упаковки по умолчанию не задаётся — укажите её для каждого товара самостоятельно, она у всех разная."
        )
        production_for_cost = read_table("production_settings")
        material_rates = read_material_cost_rates()
        if production_for_cost.empty or catalog.empty:
            st.info("Расчёт появится после настройки собственного производства и загрузки каталога товаров.")
        else:
            own_cost = production_for_cost[pd.to_numeric(production_for_cost.get("enabled", 0), errors="coerce").fillna(0).eq(1)].copy()
            own_cost = own_cost.merge(catalog[["nm_id", "product_name"]], on="nm_id", how="left")
            costs_detailed = read_table("costs")
            cost_columns = [
                "nm_id", "cost_per_wb_unit", "material_cost_rub", "packaging_cost_rub",
                "labor_cost_rub", "other_cost_rub", "forecast_material_cost_rub",
                "forecast_total_cost_rub", "forecast_rate_rub_m", "forecast_source"
            ]
            if not costs_detailed.empty:
                available_cost_columns = [c for c in cost_columns if c in costs_detailed.columns]
                own_cost = own_cost.merge(costs_detailed[available_cost_columns], on="nm_id", how="left")
            for col, default in {
                "cost_per_wb_unit": 0.0, "material_cost_rub": 0.0, "packaging_cost_rub": 0.0,
                "labor_cost_rub": 0.0, "other_cost_rub": 0.0,
                "forecast_material_cost_rub": 0.0, "forecast_total_cost_rub": 0.0,
                "forecast_rate_rub_m": 0.0,
            }.items():
                if col not in own_cost.columns:
                    own_cost[col] = default
                own_cost[col] = pd.to_numeric(own_cost[col], errors="coerce").fillna(default)
            if "forecast_source" not in own_cost.columns:
                own_cost["forecast_source"] = ""
            own_cost["forecast_source"] = own_cost["forecast_source"].fillna("").astype(str)
            own_cost["material_name"] = own_cost.get("material_name", "").fillna("").astype(str)
            own_cost["Разница, ₽"] = own_cost["forecast_total_cost_rub"] - own_cost["cost_per_wb_unit"]
            own_cost["apply_forecast"] = False

            if material_rates.empty or float(own_cost["forecast_rate_rub_m"].max() or 0) <= 0:
                st.warning(
                    "Полная цена новой партии пока не рассчитана. В заявке на рулоны заполните цену поставщика, "
                    "доставку и курс. Текущая фиксированная себестоимость при этом остаётся без изменений."
                )

            forecast_editor = st.data_editor(
                own_cost[[
                    "nm_id", "supplier_article", "product_name", "blank_type", "pack_size",
                    "material_name", "material_per_unit", "cost_per_wb_unit",
                    "forecast_rate_rub_m", "forecast_material_cost_rub",
                    "packaging_cost_rub", "labor_cost_rub", "other_cost_rub",
                    "forecast_total_cost_rub", "Разница, ₽", "forecast_source", "apply_forecast"
                ]],
                hide_index=True, use_container_width=True, key="forecast_material_cost_editor",
                disabled=[
                    "nm_id", "supplier_article", "product_name", "blank_type", "pack_size",
                    "material_name", "material_per_unit", "cost_per_wb_unit",
                    "forecast_rate_rub_m", "forecast_material_cost_rub",
                    "forecast_total_cost_rub", "Разница, ₽", "forecast_source"
                ],
                column_config={
                    "nm_id": st.column_config.NumberColumn("Артикул WB", format="%d"),
                    "supplier_article": "Артикул продавца",
                    "product_name": "Товар",
                    "blank_type": "Тип заготовки",
                    "pack_size": st.column_config.NumberColumn("В комплекте", format="%d"),
                    "material_name": "Материал / цвет",
                    "material_per_unit": st.column_config.NumberColumn("Расход, м", format="%.3f"),
                    "cost_per_wb_unit": st.column_config.NumberColumn("Текущая себестоимость, ₽", format="%.2f"),
                    "forecast_rate_rub_m": st.column_config.NumberColumn("Новая ставка, ₽/м", format="%.2f"),
                    "forecast_material_cost_rub": st.column_config.NumberColumn("Материал новой партии, ₽", format="%.2f"),
                    "packaging_cost_rub": st.column_config.NumberColumn("Упаковка, ₽", min_value=0.0, format="%.2f"),
                    "labor_cost_rub": st.column_config.NumberColumn("Работа, ₽", min_value=0.0, format="%.2f"),
                    "other_cost_rub": st.column_config.NumberColumn("Прочее, ₽", min_value=0.0, format="%.2f"),
                    "forecast_total_cost_rub": st.column_config.NumberColumn("Прогноз новой партии, ₽", format="%.2f"),
                    "Разница, ₽": st.column_config.NumberColumn(format="%+.2f"),
                    "forecast_source": "Источник прогноза",
                    "apply_forecast": st.column_config.CheckboxColumn("Применить новую цену"),
                },
            )

            forecast_buttons = st.columns(2)
            with forecast_buttons[0]:
                if st.button("Сохранить упаковку, работу и прочие расходы", use_container_width=True, key="save_forecast_components"):
                    try:
                        save_auto_cost_settings(forecast_editor[[
                            "nm_id", "supplier_article", "packaging_cost_rub", "labor_cost_rub", "other_cost_rub"
                        ]])
                        st.success("Параметры прогноза сохранены. Текущая себестоимость и прибыль не изменены.")
                        st.cache_data.clear(); st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
            with forecast_buttons[1]:
                confirm_apply_forecast = st.checkbox(
                    "Подтверждаю переход выбранных товаров на себестоимость новой партии",
                    key="confirm_apply_forecast_cost"
                )
                if st.button("Применить прогноз выбранным товарам", type="primary", use_container_width=True, key="apply_selected_forecast"):
                    if not confirm_apply_forecast:
                        st.warning("Сначала подтвердите применение новой себестоимости.")
                    else:
                        try:
                            save_auto_cost_settings(forecast_editor[[
                                "nm_id", "supplier_article", "packaging_cost_rub", "labor_cost_rub", "other_cost_rub"
                            ]])
                            applied = apply_forecast_costs(forecast_editor[["nm_id", "apply_forecast"]])
                            if applied:
                                st.success(f"Новая себестоимость применена к товарам: {applied}. Финансы пересчитаны.")
                            else:
                                st.warning("Не выбран ни один товар с рассчитанным прогнозом.")
                            st.cache_data.clear(); st.rerun()
                        except Exception as exc:
                            st.error(str(exc))

    with tabs[3]:
        st.markdown("### Параметры производства")
        st.caption(
            "Отметьте товары собственного производства, укажите тип заготовки, размер комплекта и материал/цвет. "
            "Одинаковое название материала объединяет карточки в общий план сырья. Расход материала и минимальную "
            "производственную партию каждый товар задаёт вручную — это единственные и полностью свободные поля, "
            "подходящие для любого вида продукции."
        )
        current_production = read_table("production_settings")
        if catalog.empty:
            st.info("Сначала синхронизируйте каталог товаров, чтобы настроить производственный план.")
        else:
            prod_base = catalog[["nm_id", "supplier_article", "product_name", "subject_name"]].copy()
            if not current_production.empty:
                prod_base = prod_base.merge(
                    current_production[[
                        "nm_id", "enabled", "material_per_unit", "target_days", "min_batch", "note",
                        "blank_type", "pack_size", "material_name"
                    ]],
                    on="nm_id", how="left",
                )
            if "enabled" not in prod_base.columns:
                prod_base["enabled"] = False
            defaults = {
                "material_per_unit": 0.0,
                "target_days": 21,
                "min_batch": 1,
                "note": "",
                "blank_type": "Не задано",
                "pack_size": 1,
                "material_name": "",
            }
            for col, default in defaults.items():
                if col not in prod_base.columns:
                    prod_base[col] = default
                prod_base[col] = prod_base[col].fillna(default)
            prod_base["blank_type"] = prod_base["blank_type"].replace("", "Не задано")
            prod_base["pack_size"] = pd.to_numeric(prod_base["pack_size"], errors="coerce").fillna(1).astype(int)
            prod_base["enabled"] = prod_base["enabled"].fillna(False).astype(bool)
            prod_base["material_name"] = prod_base["material_name"].fillna("").astype(str)
            inferred_materials = prod_base.apply(
                lambda r: infer_material_name(str(r.get("supplier_article", "")), str(r.get("product_name", ""))), axis=1
            )
            prod_base["material_name"] = prod_base["material_name"].where(
                prod_base["material_name"].str.strip().ne(""), inferred_materials
            )

            edited_production = st.data_editor(
                prod_base[[
                    "nm_id", "supplier_article", "product_name", "enabled",
                    "blank_type", "pack_size", "material_name", "material_per_unit",
                    "target_days", "min_batch", "note"
                ]],
                num_rows="fixed", hide_index=True, use_container_width=True,
                disabled=["nm_id", "supplier_article", "product_name"],
                column_config={
                    "nm_id": st.column_config.NumberColumn("Артикул WB", format="%d"),
                    "supplier_article": "Артикул продавца",
                    "product_name": "Товар",
                    "enabled": st.column_config.CheckboxColumn("Производим"),
                    "blank_type": st.column_config.TextColumn(
                        "Тип заготовки", help="Свободное название вашей заготовки/полуфабриката, например «Ткань А» или «Металл 2мм».", required=True
                    ),
                    "pack_size": st.column_config.NumberColumn(
                        "В комплекте, шт.", min_value=1, step=1, format="%d", required=True
                    ),
                    "material_name": st.column_config.TextColumn(
                        "Материал / цвет", help="Одинаковое название объединяет потребность нескольких карточек в одну строку сырья."
                    ),
                    "material_per_unit": st.column_config.NumberColumn(
                        "Материал на комплект, м", min_value=0.0, step=0.001, format="%.3f"
                    ),
                    "target_days": st.column_config.NumberColumn(
                        "Целевой запас, дней", min_value=7, max_value=180, step=1, format="%d"
                    ),
                    "min_batch": st.column_config.NumberColumn(
                        "Мин. партия, комплектов", min_value=1, step=1, format="%d"
                    ),
                    "note": "Примечание",
                },
            )
            if st.button("Сохранить параметры производства"):
                try:
                    normalized = edited_production
                    invalid = normalized[
                        normalized["enabled"].fillna(False).astype(bool)
                        & (normalized["blank_type"].fillna("").str.strip().isin(["", "Не задано"]))
                    ]
                    if not invalid.empty:
                        articles = ", ".join(invalid["supplier_article"].astype(str).head(8).tolist())
                        raise ValueError(f"Для производимых товаров укажите тип заготовки: {articles}")
                    save_production_settings(normalized[[
                        "nm_id", "supplier_article", "enabled", "material_per_unit",
                        "target_days", "min_batch", "note", "blank_type", "pack_size", "material_name"
                    ]])
                    st.success("Параметры производства сохранены. Нормы и минимальные партии применены.")
                    st.cache_data.clear()
                except Exception as exc:
                    st.error(str(exc))

    with tabs[4]:
        st.markdown("### Остатки сырья")
        st.caption(
            "Склад сырья ведётся единообразно по цветам/позициям — один и тот же материал можно использовать для разных "
            "типов заготовок, поэтому позиция вводится один раз. Пока флажок «Остаток указан» выключен, нулевой остаток "
            "в расчёт не подставляется. Для материалов, которые закупаются упаковками/рулонами фиксированного размера, "
            "выберите способ учёта «Упаковками» и укажите размер упаковки. Для материалов, которые вы просто считаете "
            "количеством (килограммы, литры, штуки и т.п. без фиксированной упаковки), выберите «Просто по количеству» — "
            "тогда поле «Полных упаковок» оставьте нулевым, а весь остаток указывайте в «Остаток вне упаковки»."
        )
        production_for_inventory = read_table("production_settings")
        if production_for_inventory.empty:
            st.info("Сначала сохраните параметры производства, затем появятся строки материалов и цветов.")
        else:
            production_for_inventory = production_for_inventory[
                pd.to_numeric(production_for_inventory.get("enabled", 0), errors="coerce").fillna(0).astype(int).eq(1)
            ].copy()
            if not production_for_inventory.empty and not catalog.empty:
                inventory_catalog = catalog[["nm_id", "product_name"]].copy()
                production_for_inventory = production_for_inventory.merge(inventory_catalog, on="nm_id", how="left")
            else:
                production_for_inventory["product_name"] = ""
            if "material_name" not in production_for_inventory.columns:
                production_for_inventory["material_name"] = ""
            production_for_inventory["material_name"] = production_for_inventory["material_name"].fillna("").astype(str)
            inferred_inventory_materials = production_for_inventory.apply(
                lambda r: infer_material_name(str(r.get("supplier_article", "")), str(r.get("product_name", ""))), axis=1
            )
            production_for_inventory["material_name"] = production_for_inventory["material_name"].where(
                production_for_inventory["material_name"].str.strip().ne(""), inferred_inventory_materials
            )
            raw_materials = (
                production_for_inventory[["material_name"]]
                .drop_duplicates()
                .rename(columns={"material_name": "material_name"})
            )
            raw_materials = raw_materials[raw_materials["material_name"].str.strip().ne("")].copy()
            raw_materials["material_key"] = raw_materials["material_name"].apply(material_key)
            current_inventory = read_table("material_inventory_color")
            if not current_inventory.empty:
                merge_cols = [
                    "material_key", "balance_known", "full_rolls", "partial_meters",
                    "roll_length", "note", "updated_at",
                ]
                for optional_col in ("unit", "tracking_mode", "opening_rate_rub"):
                    if optional_col in current_inventory.columns:
                        merge_cols.append(optional_col)
                raw_materials = raw_materials.merge(current_inventory[merge_cols], on="material_key", how="left")
            inventory_defaults = {
                "balance_known": False, "full_rolls": 0, "partial_meters": 0.0,
                "roll_length": 25.5, "note": "", "updated_at": "",
                "unit": "м", "tracking_mode": "packaged", "opening_rate_rub": 0.0,
            }
            for col, default in inventory_defaults.items():
                if col not in raw_materials.columns:
                    raw_materials[col] = default
                raw_materials[col] = raw_materials[col].fillna(default)
            raw_materials["balance_known"] = raw_materials["balance_known"].astype(bool)
            raw_materials["full_rolls"] = pd.to_numeric(raw_materials["full_rolls"], errors="coerce").fillna(0).astype(int)
            raw_materials["partial_meters"] = pd.to_numeric(raw_materials["partial_meters"], errors="coerce").fillna(0.0)
            raw_materials["roll_length"] = pd.to_numeric(raw_materials["roll_length"], errors="coerce").fillna(25.5)
            raw_materials.loc[
                ~raw_materials["roll_length"].astype(float).lt(NO_PACKAGE_ROLL_LENGTH / 2), "roll_length"
            ] = 25.5
            raw_materials["unit"] = raw_materials["unit"].astype(str).str.strip().replace("", "м").fillna("м")
            raw_materials["tracking_mode"] = raw_materials["tracking_mode"].astype(str).str.strip().replace("", "packaged")
            raw_materials["tracking_mode"] = raw_materials["tracking_mode"].where(
                raw_materials["tracking_mode"].isin(["packaged", "quantity"]), "packaged"
            )
            raw_materials["opening_rate_rub"] = pd.to_numeric(
                raw_materials["opening_rate_rub"], errors="coerce"
            ).fillna(0.0)

            if raw_materials.empty:
                st.info("Для производимых товаров пока не указан материал/цвет. Заполните колонку выше и сохраните параметры.")
            else:
                edited_inventory = st.data_editor(
                    raw_materials[[
                        "material_name", "balance_known", "unit", "tracking_mode",
                        "full_rolls", "roll_length", "partial_meters", "opening_rate_rub", "note",
                    ]],
                    num_rows="fixed", hide_index=True, use_container_width=True,
                    disabled=["material_name"],
                    column_config={
                        "material_name": st.column_config.TextColumn("Материал / цвет"),
                        "balance_known": st.column_config.CheckboxColumn(
                            "Остаток указан", help="Включите после фактического подсчёта сырья."
                        ),
                        "unit": st.column_config.TextColumn(
                            "Ед. измерения", help="Например: м, кг, л, шт."
                        ),
                        "tracking_mode": st.column_config.SelectboxColumn(
                            "Способ учёта",
                            options=["packaged", "quantity"],
                            help="«packaged» — упаковками/рулонами фиксированного размера. "
                                 "«quantity» — просто по количеству, без фиксированной упаковки.",
                        ),
                        "full_rolls": st.column_config.NumberColumn(
                            "Полных упаковок", min_value=0, step=1, format="%d",
                            help="Для способа учёта «quantity» оставьте 0.",
                        ),
                        "roll_length": st.column_config.NumberColumn(
                            "Размер упаковки", min_value=1.0, step=0.5, format="%.1f",
                            help="Сколько единиц (в колонке «Ед. измерения») в одной упаковке. Не используется при «quantity».",
                        ),
                        "partial_meters": st.column_config.NumberColumn(
                            "Остаток вне упаковки", min_value=0.0, step=0.1, format="%.2f",
                            help="Для способа учёта «quantity» здесь указывайте весь остаток.",
                        ),
                        "opening_rate_rub": st.column_config.NumberColumn(
                            "Ставка при инициализации, ₽", min_value=0.0, step=1.0, format="%.2f",
                            help="Цена за единицу для этого материала при инициализации FIFO-слоёв. "
                                 "0 — использовать общую ставку из раздела ниже.",
                        ),
                        "note": st.column_config.TextColumn("Примечание"),
                    },
                )
                if st.button("Сохранить остатки сырья"):
                    try:
                        save_material_inventory(edited_inventory)
                        st.success("Остатки сырья сохранены. Одна позиция учитывается общим запасом для всех типов заготовок.")
                        st.cache_data.clear()
                    except Exception as exc:
                        st.error(str(exc))

        st.divider()
        st.markdown("#### Послойная стоимость сырья — FIFO")
        st.caption(
            "Физический остаток по каждой позиции хранится как раньше, а стоимость — отдельными слоями. "
            "Сначала списывается самый ранний слой. Новые поступления создают слой по фактической цене закупки."
        )
        opening_rate = get_fifo_opening_rate()
        fifo_rate_col, fifo_action_col = st.columns([1, 2])
        with fifo_rate_col:
            opening_rate_input = st.number_input(
                "Общая ставка старого сырья по умолчанию, ₽/ед.", min_value=0.01, value=float(opening_rate),
                step=1.0, format="%.2f",
                help="Используется только для материалов, у которых выше не задана собственная «Ставка при "
                     "инициализации». Возьмите вашу текущую согласованную себестоимость материала и разделите на его расход."
            )
        with fifo_action_col:
            st.write("")
            st.write("")
            if st.button("Инициализировать / досинхронизировать FIFO-слои", use_container_width=True):
                try:
                    result = initialize_material_fifo(float(opening_rate_input))
                    set_fifo_opening_rate(float(opening_rate_input))
                    st.success(
                        f"Создано слоёв: {result['created']}; учтено старого сырья: {result['meters']:.1f} м. "
                        "Новые закупки будут добавляться отдельными слоями автоматически."
                    )
                    for warning in result.get("warnings", [])[:5]:
                        st.warning(warning)
                    st.cache_data.clear()
                except Exception as exc:
                    st.error(str(exc))

        fifo_summary = read_material_fifo_summary()
        if fifo_summary.empty:
            st.info("Сначала сохраните фактические остатки сырья, затем инициализируйте FIFO-слои.")
        else:
            fifo_summary_view = fifo_summary.copy()
            fifo_summary_view["Сверка"] = fifo_summary_view["difference_meters"].apply(
                lambda x: "Совпадает" if abs(float(x or 0)) <= 0.05 else "Требует сверки"
            )
            st.dataframe(
                fifo_summary_view[[
                    "material_name", "physical_meters", "fifo_meters", "difference_meters",
                    "active_layers", "next_fifo_rate_rub_m", "weighted_rate_rub_m", "fifo_amount_rub", "Сверка"
                ]],
                hide_index=True, use_container_width=True,
                column_config={
                    "material_name": "Материал / цвет",
                    "physical_meters": st.column_config.NumberColumn("Физически, м", format="%.1f"),
                    "fifo_meters": st.column_config.NumberColumn("В слоях, м", format="%.1f"),
                    "difference_meters": st.column_config.NumberColumn("Разница, м", format="%.1f"),
                    "active_layers": st.column_config.NumberColumn("Активных слоёв", format="%d"),
                    "next_fifo_rate_rub_m": st.column_config.NumberColumn("Следующий FIFO, ₽/м", format="%.2f"),
                    "weighted_rate_rub_m": st.column_config.NumberColumn("Средняя остатка, ₽/м", format="%.2f"),
                    "fifo_amount_rub": st.column_config.NumberColumn("Стоимость остатка, ₽", format="%.2f"),
                },
            )
            with st.expander("Показать все слои FIFO"):
                fifo_layers = read_material_cost_layers(False)
                if fifo_layers.empty:
                    st.info("Слои ещё не созданы.")
                else:
                    fifo_layers["source_date"] = pd.to_datetime(fifo_layers["source_date"], errors="coerce")
                    st.dataframe(
                        fifo_layers[[
                            "id", "material_name", "source_date", "source_type", "source_ref",
                            "original_meters", "remaining_meters", "unit_cost_rub_m",
                            "original_amount_rub", "status", "note"
                        ]],
                        hide_index=True, use_container_width=True, height=360,
                        column_config={
                            "id": st.column_config.NumberColumn("Слой", format="%d"),
                            "material_name": "Материал / цвет",
                            "source_date": st.column_config.DateColumn("Дата", format="DD.MM.YYYY"),
                            "source_type": "Источник", "source_ref": "Основание",
                            "original_meters": st.column_config.NumberColumn("Поступило, м", format="%.3f"),
                            "remaining_meters": st.column_config.NumberColumn("Осталось, м", format="%.3f"),
                            "unit_cost_rub_m": st.column_config.NumberColumn("Ставка, ₽/м", format="%.2f"),
                            "original_amount_rub": st.column_config.NumberColumn("Стоимость, ₽", format="%.2f"),
                            "status": "Статус", "note": "Примечание",
                        },
                    )

        st.divider()
        st.markdown("#### Послойная стоимость готовой продукции — FIFO")
        st.caption(
            "Инициализация создаёт стартовые слои по подтверждённым остаткам у вас, в пути и на WB. "
            "Уменьшение остатка WB автоматически не списывается: до отдельного учёта продаж оно показывается как расхождение. "
            "Стоимость старых остатков берётся из текущей базовой себестоимости артикула. "
            "Новые производственные и закупочные партии добавляются автоматически по фактической цене."
        )
        if st.button("Инициализировать / сверить FIFO готовой продукции", use_container_width=True):
            try:
                result = initialize_finished_goods_fifo(False)
                st.success(
                    f"Создано слоёв: {int(result.get('created',0))}; учтено {int(result.get('units',0))} ед.; "
                    f"автоматических списаний WB не выполнялось."
                )
                for warning in list(result.get("warnings", []) or [])[:8]:
                    st.warning(warning)
                st.cache_data.clear()
            except Exception as exc:
                st.error(str(exc))
        fg_settings_summary = read_finished_goods_fifo_summary()
        if fg_settings_summary.empty:
            st.info("Нет данных для послойного учёта готовой продукции.")
        else:
            for col in ["ready_physical", "ready_layer_units", "inbound_physical", "inbound_layer_units", "wb_physical", "wb_layer_units", "ready_difference", "inbound_difference", "wb_difference", "active_layers"]:
                fg_settings_summary[col] = pd.to_numeric(fg_settings_summary.get(col, 0), errors="coerce").fillna(0)
            issues = fg_settings_summary[
                fg_settings_summary[["ready_difference", "inbound_difference", "wb_difference"]].abs().max(axis=1) > 0
            ].copy()
            total_physical = int((fg_settings_summary["ready_physical"] + fg_settings_summary["inbound_physical"] + fg_settings_summary["wb_physical"]).sum())
            total_layered = int((fg_settings_summary["ready_layer_units"] + fg_settings_summary["inbound_layer_units"] + fg_settings_summary["wb_layer_units"]).sum())
            cards = st.columns(3)
            with cards[0]: kpi_card("Физически", num(total_physical), "Готово + в пути + WB")
            with cards[1]: kpi_card("В слоях", num(total_layered), "Послойная стоимость")
            with cards[2]: kpi_card("Расхождения", num(len(issues)), "Артикулов")
            if issues.empty:
                st.success("Физические остатки и FIFO-слои готовой продукции совпадают.")
            else:
                st.dataframe(
                    issues[["supplier_article", "product_name", "ready_difference", "inbound_difference", "wb_difference"]],
                    hide_index=True, use_container_width=True,
                    column_config={
                        "supplier_article": "Артикул продавца", "product_name": "Товар",
                        "ready_difference": st.column_config.NumberColumn("Разница готового", format="%d"),
                        "inbound_difference": st.column_config.NumberColumn("Разница в пути", format="%d"),
                        "wb_difference": st.column_config.NumberColumn("Разница WB", format="%d"),
                    },
                )
            with st.expander("Все слои готовой продукции"):
                fg_layers = read_finished_goods_cost_layers(False)
                if fg_layers.empty:
                    st.info("Слои пока не созданы.")
                else:
                    st.dataframe(
                        fg_layers[[
                            "id", "supplier_article", "product_name", "source_type", "source_date",
                            "original_units", "ready_units", "inbound_units", "wb_units", "unit_cost_rub", "status"
                        ]], hide_index=True, use_container_width=True, height=360,
                        column_config={
                            "id": st.column_config.NumberColumn("Слой", format="%d"),
                            "supplier_article": "Артикул продавца", "product_name": "Товар", "source_type": "Источник",
                            "source_date": st.column_config.DateColumn("Дата", format="DD.MM.YYYY"),
                            "original_units": st.column_config.NumberColumn("Поступило", format="%d"),
                            "ready_units": st.column_config.NumberColumn("Готово", format="%d"),
                            "inbound_units": st.column_config.NumberColumn("В пути", format="%d"),
                            "wb_units": st.column_config.NumberColumn("На WB", format="%d"),
                            "unit_cost_rub": st.column_config.NumberColumn("Ставка, ₽", format="%.2f"),
                            "status": "Статус",
                        },
                    )

        st.divider()
        st.markdown("#### FIFO-себестоимость продаж и возвратов")
        st.caption(
            "При первом включении уже загруженные операции фиксируются как историческая база и не списывают текущий остаток повторно. "
            "Каждая новая продажа списывает одну единицу с самого раннего слоя на WB. Возврат восстанавливает исходный слой по SRID, когда связь доступна."
        )
        sales_fifo_status = sales_fifo_tracking_status()
        sf_cols = st.columns(5)
        with sf_cols[0]: kpi_card("Статус", "Включён" if sales_fifo_status.get("initialized") else "Не включён", str(sales_fifo_status.get("initialized_at") or "")[:19])
        with sf_cols[1]: kpi_card("Историческая база", num(sales_fifo_status.get("baseline_rows", 0)), "Операций до FIFO")
        with sf_cols[2]: kpi_card("Продажи / возвраты", f"{num(sales_fifo_status.get('sales_applied',0))} / {num(sales_fifo_status.get('returns_applied',0))}", "Точно обработано")
        with sf_cols[3]: kpi_card("Ошибки", num(sales_fifo_status.get("errors", 0)), "Можно повторить")
        with sf_cols[4]: kpi_card("Последняя дата", str(sales_fifo_status.get("last_event_date") or "—")[:10], "Операция API")
        sf_actions = st.columns(3)
        with sf_actions[0]:
            if st.button("Инициализировать FIFO продаж с текущего момента", use_container_width=True, disabled=bool(sales_fifo_status.get("initialized"))):
                try:
                    res = initialize_sales_fifo_tracking()
                    st.success(f"FIFO продаж включён. Историческая база: {int(res.get('baseline',0))} операций.")
                    st.cache_data.clear(); st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        with sf_actions[1]:
            if st.button("Обработать новые продажи и возвраты", use_container_width=True, disabled=not bool(sales_fifo_status.get("initialized"))):
                try:
                    res = process_sales_fifo_events()
                    st.success(f"Обработано: {int(res.get('processed',0))}; продажи: {int(res.get('sales',0))}; возвраты: {int(res.get('returns',0))}; ошибки: {int(res.get('errors',0))}.")
                    for warning in list(res.get("warnings",[]) or [])[:6]: st.warning(warning)
                    st.cache_data.clear(); st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        with sf_actions[2]:
            if st.button("Повторить ошибочные операции", use_container_width=True, disabled=int(sales_fifo_status.get("errors",0))<=0):
                try:
                    res = retry_sales_fifo_errors()
                    st.success(f"Повторно обработано: {int(res.get('processed',0))}; ошибок осталось: {int(res.get('errors',0))}.")
                    st.cache_data.clear(); st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        st.info(
            "После обычной синхронизации новые операции обрабатываются автоматически. "
            "Если фактический остаток WB и слои расходятся из-за задержек API или внешних поставок, используйте сверку ниже; сверочные списания не считаются продажами."
        )
        sf_recent = read_sales_fifo_events(50)
        if not sf_recent.empty:
            sf_errors = sf_recent[sf_recent["status"].eq("error")].copy()
            if not sf_errors.empty:
                st.dataframe(sf_errors[["event_date", "supplier_article", "event_type", "sale_id", "note"]], hide_index=True, use_container_width=True)

        st.divider()
        st.markdown("#### Контроль расхождений FIFO")
        st.caption(
            "v5.9 отделяет безопасную локальную сверку от диагностических расхождений WB. "
            "WB-строки автоматически не списываются: они могут отражать товары в пути, агрегированный снимок или внешнюю утрату на складе."
        )
        recon_settings = fifo_reconciliation_status()
        rset_cols = st.columns(5)
        with rset_cols[0]: kpi_card("Расхождений", num(recon_settings.get("current_lines",0)), f"Артикулов: {int(recon_settings.get('current_articles',0) or 0)}")
        with rset_cols[1]: kpi_card("Диагн. +", num(recon_settings.get("current_added",0)), "Единиц")
        with rset_cols[2]: kpi_card("Диагн. −", num(recon_settings.get("current_removed",0)), "Единиц")
        with rset_cols[3]: kpi_card("Заблокировано WB", num(recon_settings.get("current_blocked_units",0)), "Не автосписывается")
        with rset_cols[4]: kpi_card("Внешний разрыв*", num(recon_settings.get("diagnostic_external_gap_units",0)), "Диагностический ориентир")
        if int(recon_settings.get("current_lines",0) or 0) > 0:
            if int(recon_settings.get("current_blocked_units",0) or 0) > 0:
                st.warning("Есть WB-расхождения. Откройте Остатки → Сверка FIFO; обычную управленческую сверку WB не проводите.")
            else:
                st.warning("Есть локальные расхождения. Подробная таблица находится в Остатки → Сверка FIFO.")
        else:
            st.success("Текущих диагностических расхождений нет.")
        st.info(
            "Generic-сверка теперь может менять только безопасные локальные слои. Слои на WB защищены от автоматического списания/досоздания; "
            "для подтверждённых утрат будет нужен отдельный тип движения «утрата на складе WB»."
        )

    with tabs[5]:
        st.markdown("### Готовая продукция и поставки на WB")
        st.caption(
            "Укажите комплекты, которые уже произведены и находятся у вас, а также товары, уже отправленные на WB. "
            "Неотмеченные остатки считаются неизвестными и не уменьшают производственный план."
        )
        enabled_for_pipeline = read_table("production_settings")
        if enabled_for_pipeline.empty or catalog.empty:
            st.info("Сначала синхронизируйте каталог и сохраните параметры производства.")
        else:
            enabled_for_pipeline = enabled_for_pipeline[
                pd.to_numeric(enabled_for_pipeline.get("enabled", 0), errors="coerce").fillna(0).astype(int).eq(1)
            ][["nm_id", "supplier_article"]].copy()
            pipeline_base = enabled_for_pipeline.merge(
                catalog[["nm_id", "product_name"]], on="nm_id", how="left"
            )
            current_pipeline = read_table("product_pipeline")
            if not current_pipeline.empty:
                pipeline_base = pipeline_base.merge(
                    current_pipeline[[
                        "nm_id", "local_known", "ready_units", "inbound_known",
                        "inbound_units", "inbound_date", "note"
                    ]], on="nm_id", how="left"
                )
            pipeline_defaults = {
                "local_known": False, "ready_units": 0, "inbound_known": False,
                "inbound_units": 0, "inbound_date": pd.NaT, "note": "",
            }
            for col, default in pipeline_defaults.items():
                if col not in pipeline_base.columns:
                    pipeline_base[col] = default
                if col == "inbound_date":
                    pipeline_base[col] = pd.to_datetime(pipeline_base[col], errors="coerce")
                else:
                    pipeline_base[col] = pipeline_base[col].fillna(default)
            pipeline_base["local_known"] = pipeline_base["local_known"].astype(bool)
            pipeline_base["inbound_known"] = pipeline_base["inbound_known"].astype(bool)
            pipeline_base["ready_units"] = pd.to_numeric(pipeline_base["ready_units"], errors="coerce").fillna(0).astype(int)
            pipeline_base["inbound_units"] = pd.to_numeric(pipeline_base["inbound_units"], errors="coerce").fillna(0).astype(int)

            edited_pipeline = st.data_editor(
                pipeline_base[[
                    "nm_id", "supplier_article", "product_name", "local_known", "ready_units",
                    "inbound_known", "inbound_units", "inbound_date", "note"
                ]],
                num_rows="fixed", hide_index=True, use_container_width=True,
                disabled=["nm_id", "supplier_article", "product_name"],
                column_config={
                    "nm_id": st.column_config.NumberColumn("Артикул WB", format="%d"),
                    "supplier_article": "Артикул продавца",
                    "product_name": "Товар",
                    "local_known": st.column_config.CheckboxColumn("Готовый остаток указан"),
                    "ready_units": st.column_config.NumberColumn(
                        "Готово на производстве, компл.", min_value=0, step=1, format="%d"
                    ),
                    "inbound_known": st.column_config.CheckboxColumn("Поставка указана"),
                    "inbound_units": st.column_config.NumberColumn(
                        "В пути на WB, компл.", min_value=0, step=1, format="%d"
                    ),
                    "inbound_date": st.column_config.DateColumn("Ожидаемая дата", format="DD.MM.YYYY"),
                    "note": st.column_config.TextColumn("Примечание"),
                },
            )
            pipeline_btn_1, pipeline_btn_2 = st.columns([1.2, 1.8])
            with pipeline_btn_1:
                if st.button("Сохранить готовую продукцию и поставки", use_container_width=True):
                    try:
                        save_product_pipeline(edited_pipeline[[
                            "nm_id", "supplier_article", "local_known", "ready_units",
                            "inbound_known", "inbound_units", "inbound_date", "note"
                        ]])
                        st.success("Готовая продукция и поставки сохранены. Производственный план пересчитан.")
                        st.cache_data.clear()
                    except Exception as exc:
                        st.error(str(exc))
            with pipeline_btn_2:
                if st.button(
                    "Незаполненное считать нулём",
                    help="Отмечает неизвестный готовый остаток и неизвестную поставку как подтверждённый ноль. Уже заполненные значения не меняются.",
                    use_container_width=True,
                ):
                    try:
                        zero_pipeline = edited_pipeline.copy()
                        missing_local = ~zero_pipeline["local_known"].astype(bool)
                        missing_inbound = ~zero_pipeline["inbound_known"].astype(bool)
                        zero_pipeline.loc[missing_local, "local_known"] = True
                        zero_pipeline.loc[missing_local, "ready_units"] = 0
                        zero_pipeline.loc[missing_inbound, "inbound_known"] = True
                        zero_pipeline.loc[missing_inbound, "inbound_units"] = 0
                        zero_pipeline.loc[missing_inbound, "inbound_date"] = pd.NaT
                        save_product_pipeline(zero_pipeline[[
                            "nm_id", "supplier_article", "local_known", "ready_units",
                            "inbound_known", "inbound_units", "inbound_date", "note"
                        ]])
                        st.success("Все незаполненные готовые остатки и поставки сохранены как нулевые.")
                        st.cache_data.clear()
                    except Exception as exc:
                        st.error(str(exc))

    with tabs[6]:
        st.markdown("### Производственная мощность")
        st.caption(
            "Укажите фактическое количество отдельных изделий за рабочий день, рабочий график и срок до появления "
            "готовой партии на WB. Сначала календарь раздаёт срочным товарам аварийный запас, затем закрывает полный план."
        )
        capacity = get_production_capacity()
        weekday_pairs = [
            (0, "Пн"), (1, "Вт"), (2, "Ср"), (3, "Чт"), (4, "Пт"), (5, "Сб"), (6, "Вс")
        ]
        weekday_by_id = dict(weekday_pairs)
        try:
            saved_workdays = {int(x) for x in str(capacity.get("workdays", "0,1,2,3,4,5")).split(",") if str(x).strip()}
        except ValueError:
            saved_workdays = {0, 1, 2, 3, 4, 5}
        cap_c1, cap_c2, cap_c3 = st.columns([1, 1.15, 1.85])
        with cap_c1:
            capacity_known_ui = st.checkbox(
                "Мощность указана", value=bool(int(capacity.get("capacity_known", 0) or 0))
            )
            pieces_per_day_ui = st.number_input(
                "Изделий в рабочий день", min_value=0, step=1,
                value=max(0, int(capacity.get("pieces_per_day", 0) or 0))
            )
            emergency_cover_days_ui = st.number_input(
                "Аварийный запас, дней", min_value=1, max_value=30, step=1,
                value=max(1, int(capacity.get("emergency_cover_days", 7) or 7)),
                help="Сначала каждый срочный артикул получает партию примерно на этот срок."
            )
        with cap_c2:
            horizon_days_ui = st.number_input(
                "Горизонт планирования, дней", min_value=7, max_value=90, step=1,
                value=max(7, int(capacity.get("horizon_days", 14) or 14))
            )
            fulfillment_lead_days_ui = st.number_input(
                "Стандартная FBO: до появления на WB, дней", min_value=0, max_value=30, step=1,
                value=max(0, int(capacity.get("fulfillment_lead_days", 0) or 0)),
                help="Обычная доставка, приёмка и фактическое появление остатка для покупателей."
            )
            expedited_fbo_lead_days_ui = st.number_input(
                "Ускоренная FBO, дней", min_value=0, max_value=30, step=1,
                value=max(0, int(capacity.get("expedited_fbo_lead_days", 3) or 3)),
                help="Используется для расчётной даты поступления в режиме исполнения отгрузок."
            )
        with cap_c3:
            selected_day_names = st.multiselect(
                "Рабочие дни",
                options=[name for _, name in weekday_pairs],
                default=[weekday_by_id[d] for d in sorted(saved_workdays) if d in weekday_by_id],
            )
            fbs_lead_days_ui = st.number_input(
                "FBS: срок до доступности покупателю, дней", min_value=0, max_value=14, step=1,
                value=max(0, int(capacity.get("fbs_lead_days", 0) or 0)),
                help="Обычно 0: товар продаётся со склада продавца. При необходимости укажите внутреннюю задержку."
            )
            capacity_note_ui = st.text_input("Примечание к мощности", value=str(capacity.get("note", "") or ""))
        if st.button("Сохранить производственную мощность"):
            selected_ids = [day_id for day_id, name in weekday_pairs if name in selected_day_names]
            if capacity_known_ui and pieces_per_day_ui <= 0:
                st.error("Укажите производительность больше нуля или снимите флажок «Мощность указана».")
            elif not selected_ids:
                st.error("Выберите хотя бы один рабочий день.")
            else:
                save_production_capacity(
                    capacity_known=capacity_known_ui,
                    pieces_per_day=int(pieces_per_day_ui),
                    workdays=selected_ids,
                    horizon_days=int(horizon_days_ui),
                    fulfillment_lead_days=int(fulfillment_lead_days_ui),
                    emergency_cover_days=int(emergency_cover_days_ui),
                    expedited_fbo_lead_days=int(expedited_fbo_lead_days_ui),
                    fbs_lead_days=int(fbs_lead_days_ui),
                    note=capacity_note_ui,
                )
                st.success("Производственная мощность сохранена. Календарный план обновлён.")
                st.cache_data.clear()

    with tabs[7]:
        st.markdown("### Резервные копии и восстановление")
        st.caption(
            "База и настройки автоматически архивируются один раз в день. Хранятся 14 последних копий. "
            "Токен WB API в резервную копию не включается и при восстановлении не меняется."
        )
        backup_flash = st.session_state.pop("backup_flash", None)
        if backup_flash:
            level, text = backup_flash
            if level == "success":
                st.success(text)
            else:
                st.error(text)

        backups = list_backups(limit=14)
        latest = backups[0] if backups else None
        backup_cols = st.columns(3)
        with backup_cols[0]:
            kpi_card(
                "Последняя копия",
                latest["modified_at"].strftime("%d.%m.%Y %H:%M") if latest else "Нет",
                latest["kind"] if latest else "Создайте первую копию",
            )
        with backup_cols[1]:
            kpi_card("Копий сохранено", num(len(backups)), "Автоматические и ручные")
        with backup_cols[2]:
            total_backup_size = sum(int(row.get("size_bytes", 0) or 0) for row in backups)
            kpi_card("Размер архива", f"{total_backup_size / 1024 / 1024:.1f} МБ", "Последние 14 копий")

        backup_action_cols = st.columns([1, 2])
        with backup_action_cols[0]:
            if st.button("Создать резервную копию", type="primary", use_container_width=True):
                try:
                    path = create_backup(kind="manual")
                    st.session_state["manual_backup_path"] = str(path)
                    st.success(f"Копия создана: {path.name}")
                except Exception as exc:
                    st.error(f"Не удалось создать копию: {exc}")
        manual_backup_path = st.session_state.get("manual_backup_path")
        if manual_backup_path:
            try:
                backup_path_obj = Path(manual_backup_path)
                with backup_action_cols[1]:
                    st.download_button(
                        "Скачать созданную копию",
                        data=backup_bytes(backup_path_obj),
                        file_name=backup_path_obj.name,
                        mime="application/zip",
                        use_container_width=True,
                    )
            except Exception:
                st.session_state.pop("manual_backup_path", None)

        if backups:
            backup_table = pd.DataFrame(backups)
            backup_table["Размер, МБ"] = backup_table["size_bytes"].astype(float) / 1024 / 1024
            backup_table["Дата"] = pd.to_datetime(backup_table["modified_at"], errors="coerce")
            st.dataframe(
                backup_table[["Дата", "kind", "name", "Размер, МБ"]].rename(columns={
                    "kind": "Тип", "name": "Файл"
                }),
                hide_index=True, use_container_width=True, height=min(300, 72 + 35 * len(backup_table)),
                column_config={
                    "Дата": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
                    "Размер, МБ": st.column_config.NumberColumn(format="%.2f"),
                },
            )

        st.markdown("#### Восстановление")
        restore_file = st.file_uploader(
            "Выберите ZIP-копию MARKETSHELPER", type=["zip"], key="restore_backup_file"
        )
        if restore_file is not None:
            restore_payload = restore_file.getvalue()
            try:
                restore_meta = inspect_backup(restore_payload)
                st.info(
                    f"Копия создана: {restore_meta['created_at']}. "
                    f"Настройки: {'есть' if restore_meta['contains_settings'] else 'нет'}. "
                    "Токен API не будет изменён."
                )
                restore_settings_ui = st.checkbox(
                    "Восстановить также настройки приложения", value=True, key="restore_settings_ui"
                )
                confirm_restore = st.checkbox(
                    "Понимаю, что текущая база будет заменена", key="confirm_restore_backup"
                )
                if st.button(
                    "Восстановить резервную копию",
                    disabled=not confirm_restore,
                    use_container_width=True,
                ):
                    result = restore_backup(restore_payload, restore_settings=restore_settings_ui)
                    st.session_state["backup_flash"] = (
                        "success",
                        result["message"] + " Перед заменой создана страховая копия.",
                    )
                    st.cache_data.clear()
                    st.rerun()
            except Exception as exc:
                st.error(f"Архив не прошёл проверку: {exc}")

        with st.expander("Тестовые данные"):
            st.warning("Демо-данные предназначены только для просмотра интерфейса. При синхронизации реального кабинета они удаляются автоматически.")
            demo_c1, demo_c2 = st.columns(2)
            with demo_c1:
                if st.button("Загрузить демо-данные"):
                    with st.spinner("Создаю пример дашборда..."):
                        generate_demo()
                    st.success("Демо-данные загружены. Откройте раздел «Обзор».")
            with demo_c2:
                if st.button("Удалить демо-данные"):
                    clear_demo_data()
                    st.success("Демо-данные удалены")
                    st.cache_data.clear()
