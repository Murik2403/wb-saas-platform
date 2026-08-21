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
from config import (
    load_settings,
    save_settings,
)
from datetime import (
    date,
    datetime,
    timedelta,
)
from db import (
    PAYMENT_METHODS,
    PROCUREMENT_STATUSES,
    create_procurement_order,
    delete_procurement_order,
    post_procurement_receipt,
    read_procurement_items,
    read_procurement_movements,
    read_procurement_orders,
    read_procurement_payments,
    read_suppliers,
    read_table,
    record_procurement_payment,
    refresh_auto_costs,
    save_supplier,
    supplier_defaults,
    undo_inventory_movement,
    undo_procurement_payment,
    update_procurement_items,
    update_procurement_order,
)
from zoneinfo import (
    ZoneInfo,
)


def render(ctx: dict) -> None:
    data = ctx['data']

    st.markdown("### Закупки")
    st.caption(
        "Заявки на сырьё и закупаемые товары, платежи, сроки поставки и приёмка. "
        "После проведения приёмки сырьё автоматически увеличивает склад по цвету, а товар — готовый остаток."
    )
    procurement_orders = read_procurement_orders()
    procurement_items_all = read_procurement_items()
    procurement_payments = read_procurement_payments()
    suppliers = read_suppliers()
    active_suppliers = suppliers[suppliers["active"].astype(int).eq(1)].copy() if not suppliers.empty else pd.DataFrame()
    material_recommendations, product_recommendations = procurement_recommendations(data.products)
    today_proc = datetime.now(ZoneInfo("Europe/Moscow")).date()

    if procurement_orders.empty:
        active_orders = overdue_orders = transit_orders = unpaid_orders = pd.DataFrame()
        active_amount = 0.0
    else:
        procurement_orders = procurement_orders.copy()
        procurement_orders["expected_dt"] = pd.to_datetime(procurement_orders["expected_date"], errors="coerce").dt.date
        procurement_orders["payment_due_dt"] = pd.to_datetime(procurement_orders["payment_due_date"], errors="coerce").dt.date
        closed_statuses = {"Получено", "Отменено"}
        active_orders = procurement_orders[~procurement_orders["status"].isin(closed_statuses)].copy()
        overdue_orders = active_orders[active_orders["expected_dt"].notna() & (active_orders["expected_dt"] < today_proc)].copy()
        transit_orders = active_orders[active_orders["status"].isin(["В пути", "Частично получено"])].copy()
        unpaid_orders = active_orders[pd.to_numeric(active_orders.get("outstanding_amount", 0), errors="coerce").fillna(0).gt(0.01)].copy()
        active_amount = float(pd.to_numeric(active_orders.get("outstanding_amount", 0), errors="coerce").fillna(0).sum())

    due_7_amount = 0.0
    if not unpaid_orders.empty:
        due_mask = unpaid_orders["payment_due_dt"].notna() & (unpaid_orders["payment_due_dt"] <= today_proc + timedelta(days=7))
        due_7_amount = float(pd.to_numeric(unpaid_orders.loc[due_mask].get("outstanding_amount", 0), errors="coerce").fillna(0).sum())

    procurement_kpis = st.columns(6)
    with procurement_kpis[0]: kpi_card("Активные закупки", num(len(active_orders)), "Не получены и не отменены")
    with procurement_kpis[1]: kpi_card("В пути", num(len(transit_orders)), "Включая частичные поставки")
    with procurement_kpis[2]: kpi_card("Просрочено", num(len(overdue_orders)), "Ожидаемая дата прошла")
    with procurement_kpis[3]: kpi_card("К оплате", money(active_amount), "Остаток после проведённых платежей")
    with procurement_kpis[4]: kpi_card("До 7 дней", money(due_7_amount), "Платёжный календарь")
    with procurement_kpis[5]: kpi_card("Поставщики", num(len(active_suppliers)), "Активные в справочнике")

    procurement_flash = st.session_state.pop("procurement_flash", None)
    if procurement_flash:
        level, text = procurement_flash
        getattr(st, level if level in {"success", "warning", "error", "info"} else "info")(text)

    procurement_tabs = st.tabs(["Рекомендации", "Создать закупку", "Заявки", "Приёмка", "Платежи", "Поставщики", "Журнал", "Сводный план"])

    with procurement_tabs[0]:
        st.markdown("#### Сырьё к закупке")
        if material_recommendations.empty:
            st.success("Дефицита сырья по текущему производственному плану нет.")
        else:
            material_view = material_recommendations[[
                "Материал / цвет", "unit", "Нужно материала, м", "На складе, м", "Не хватает, м",
                "Упаковок докупить", "Артикул продавца"
            ]].rename(columns={"unit": "Ед."}).copy()
            st.dataframe(
                material_view, hide_index=True, use_container_width=True,
                column_config={
                    "Нужно материала, м": st.column_config.NumberColumn(format="%.1f"),
                    "На складе, м": st.column_config.NumberColumn(format="%.1f"),
                    "Не хватает, м": st.column_config.NumberColumn(format="%.1f"),
                    "Упаковок докупить": st.column_config.NumberColumn(format="%d"),
                },
            )
            if st.button("Создать заявку на сырьё из рекомендаций", type="primary", key="auto_material_procurement"):
                is_packaged_row = material_recommendations.get("roll_length", 25.5).apply(is_packaged_material)
                auto_quantity = pd.to_numeric(material_recommendations["Упаковок докупить"], errors="coerce").fillna(0).where(
                    is_packaged_row, pd.to_numeric(material_recommendations["Не хватает, м"], errors="coerce").fillna(0)
                )
                auto_unit = material_recommendations.get("unit", "м").fillna("м").astype(str).str.strip().replace("", "м")
                auto_unit = auto_unit.mask(is_packaged_row, "рулон")
                auto_items = pd.DataFrame({
                    "material_name": material_recommendations["Материал / цвет"],
                    "quantity": auto_quantity,
                    "unit": auto_unit,
                    "roll_length": pd.to_numeric(material_recommendations.get("roll_length", 25.5), errors="coerce").fillna(25.5),
                    "unit_price": 0.0,
                    "note": material_recommendations["Артикул продавца"].map(lambda x: f"Для производственного плана: {x}"),
                })
                try:
                    order_id = create_procurement_order(
                        "Сырьё", "", "Запланировано", today_proc, today_proc + timedelta(days=3),
                        today_proc + timedelta(days=7), "Создано автоматически из дефицита сырья", auto_items,
                        source_key=f"auto-material-{today_proc.isoformat()}",
                    )
                    st.session_state["procurement_flash"] = ("success", f"Создана заявка на сырьё №{order_id}. Укажите поставщика и цены во вкладке «Заявки».")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        st.markdown("#### Закупаемые товары")
        if product_recommendations.empty:
            st.success("Закупаемых товаров с критическим остатком нет.")
        else:
            st.dataframe(
                product_recommendations, hide_index=True, use_container_width=True,
                column_config={
                    "Продаж/день": st.column_config.NumberColumn(format="%.2f"),
                    "Запас WB, дней": st.column_config.NumberColumn(format="%.1f"),
                    "Ориентировочно заказать, шт.": st.column_config.NumberColumn(format="%d"),
                },
            )
            if st.button("Создать заявку на товары из рекомендаций", type="primary", key="auto_product_procurement"):
                auto_items = product_recommendations.rename(columns={"Ориентировочно заказать, шт.": "quantity"})[[
                    "Артикул WB", "Артикул продавца", "Товар", "quantity"
                ]].rename(columns={"Артикул WB": "nm_id", "Артикул продавца": "supplier_article", "Товар": "product_name"})
                auto_items["unit_price"] = 0.0
                auto_items["note"] = "Критический остаток WB"
                try:
                    order_id = create_procurement_order(
                        "Товар", "", "Запланировано", today_proc, today_proc + timedelta(days=3),
                        today_proc + timedelta(days=14), "Создано автоматически по критическим остаткам", auto_items,
                        source_key=f"auto-products-{today_proc.isoformat()}",
                    )
                    st.session_state["procurement_flash"] = ("success", f"Создана заявка на товары №{order_id}. Укажите поставщика и цены во вкладке «Заявки».")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    with procurement_tabs[1]:
        procurement_type_ui = st.segmented_control("Тип закупки", ["Сырьё", "Товар"], default="Сырьё", key="new_procurement_type")
        supplier_options = {int(r["id"]): str(r["name"]) for _, r in active_suppliers.iterrows()} if not active_suppliers.empty else {}
        supplier_choice_values = [0] + list(supplier_options)
        new_supplier_id = st.selectbox(
            "Поставщик из справочника", supplier_choice_values,
            format_func=lambda value: "— выбрать или ввести ниже —" if int(value) == 0 else supplier_options[int(value)],
            key="new_proc_supplier_id",
        )
        selected_supplier_defaults = supplier_defaults(int(new_supplier_id)) if int(new_supplier_id or 0) > 0 else {}
        default_payment_terms = max(0, int(selected_supplier_defaults.get("payment_terms_days", 3) or 3))
        default_lead = max(0, int(selected_supplier_defaults.get("lead_time_days", 7 if procurement_type_ui == "Сырьё" else 14) or 0))
        default_currency = str(selected_supplier_defaults.get("default_currency", "RUB") or "RUB").upper()
        if default_currency not in {"RUB", "USD", "CNY", "EUR"}:
            default_currency = "RUB"
        new_header_cols = st.columns(3)
        with new_header_cols[0]:
            custom_supplier = st.text_input(
                "Новый поставщик / свободное название",
                value="" if int(new_supplier_id or 0) > 0 else "",
                placeholder="Например, Эмма",
                key="new_proc_supplier_custom",
            )
            new_supplier = supplier_options.get(int(new_supplier_id or 0), "") or custom_supplier.strip()
            new_status = st.selectbox("Статус", ["Запланировано", "Заказано"], key="new_proc_status")
        with new_header_cols[1]:
            new_order_date = st.date_input("Дата заявки", value=today_proc, format="DD.MM.YYYY", key="new_proc_order_date")
            new_payment_date = st.date_input(
                "Оплатить до", value=today_proc + timedelta(days=default_payment_terms),
                format="DD.MM.YYYY", key=f"new_proc_payment_date_{int(new_supplier_id or 0)}"
            )
        with new_header_cols[2]:
            new_expected_date = st.date_input(
                "Ожидаемая поставка", value=today_proc + timedelta(days=default_lead),
                format="DD.MM.YYYY", key=f"new_proc_expected_date_{int(new_supplier_id or 0)}_{procurement_type_ui}"
            )
            new_note = st.text_input("Примечание", key="new_proc_note")
        price_header_cols = st.columns(2)
        with price_header_cols[0]:
            currency_options = ["RUB", "USD", "CNY", "EUR"]
            new_currency = st.selectbox(
                "Валюта цены поставщика", currency_options,
                index=currency_options.index(default_currency),
                key=f"new_proc_currency_{int(new_supplier_id or 0)}",
            )
        with price_header_cols[1]:
            default_rate = 1.0 if new_currency == "RUB" else (80.0 if new_currency == "USD" else (11.2 if new_currency == "CNY" else 92.0))
            new_exchange_rate = st.number_input(
                "Курс, ₽ за 1 ед. валюты", min_value=0.0001, value=float(default_rate), step=0.1,
                format="%.4f", key=f"new_proc_rate_{int(new_supplier_id or 0)}_{new_currency}",
            )
        if selected_supplier_defaults:
            st.caption(
                f"Условия поставщика: оплата через {default_payment_terms} дн., поставка около {default_lead} дн. "
                f"Контакт: {selected_supplier_defaults.get('contact_person') or 'не указан'}; "
                f"{selected_supplier_defaults.get('messenger') or selected_supplier_defaults.get('phone') or ''}"
            )

        new_items_df = pd.DataFrame()
        if procurement_type_ui == "Сырьё":
            raw_inventory = read_table("material_inventory_color")
            known_materials = sorted(set(raw_inventory.get("material_name", pd.Series(dtype=str)).dropna().astype(str).str.strip()) - {""}) if not raw_inventory.empty else []
            recommended_materials = material_recommendations.get("Материал / цвет", pd.Series(dtype=str)).dropna().astype(str).tolist() if not material_recommendations.empty else []
            selected_materials = st.multiselect(
                "Материалы / цвета", known_materials,
                default=[m for m in recommended_materials if m in known_materials], key="new_proc_materials"
            )
            custom_material = st.text_input("Дополнительный материал / цвет", key="new_proc_custom_material")
            if custom_material.strip() and custom_material.strip() not in selected_materials:
                selected_materials.append(custom_material.strip())
            raw_rows = []
            raw_map = raw_inventory.set_index("material_name").to_dict("index") if not raw_inventory.empty and "material_name" in raw_inventory.columns else {}
            rec_map = material_recommendations.set_index("Материал / цвет").to_dict("index") if not material_recommendations.empty else {}
            for material_name_value in selected_materials:
                inv_row = raw_map.get(material_name_value, {})
                rec_row = rec_map.get(material_name_value, {})
                row_roll_length = float(inv_row.get("roll_length", 25.5) or 25.5)
                row_is_packaged = is_packaged_material(row_roll_length)
                if row_is_packaged:
                    row_quantity = max(1, int(rec_row.get("Упаковок докупить", 1) or 1))
                    row_unit = "рулон"
                else:
                    row_quantity = max(0.0, float(rec_row.get("Не хватает, м", 0) or 0)) or 1.0
                    row_unit = str(inv_row.get("unit", "м") or "м").strip() or "м"
                raw_rows.append({
                    "material_name": material_name_value,
                    "quantity": row_quantity,
                    "unit": row_unit,
                    "roll_length": row_roll_length,
                    "supplier_unit_price": 0.0,
                    "delivery_unit_foreign": 0.0,
                    "extra_unit_rub": 0.0,
                    "exchange_rate": float(new_exchange_rate),
                    "unit_price": 0.0,
                    "note": "",
                })
            new_items_df = st.data_editor(
                pd.DataFrame(raw_rows), hide_index=True, use_container_width=True, num_rows="dynamic",
                key="new_raw_proc_items",
                column_config={
                    "material_name": st.column_config.TextColumn("Материал / цвет", required=True),
                    "quantity": st.column_config.NumberColumn("Количество", min_value=0.0, step=1.0),
                    "unit": st.column_config.SelectboxColumn(
                        "Ед.", options=["рулон", "м", "кг", "л", "шт", "упаковка"],
                        help="«рулон» — закупка пересчитывается через «Размер упаковки» ниже. Любая другая единица — "
                             "количество указывается напрямую.",
                    ),
                    "roll_length": st.column_config.NumberColumn(
                        "Размер упаковки", min_value=0.1, step=0.1, format="%.2f",
                        help="Используется только при единице «рулон».",
                    ),
                    "supplier_unit_price": st.column_config.NumberColumn(f"Цена поставщика, {new_currency}", min_value=0.0, step=1.0, format="%.4f"),
                    "delivery_unit_foreign": st.column_config.NumberColumn(f"Доставка на ед., {new_currency}", min_value=0.0, step=1.0, format="%.4f"),
                    "extra_unit_rub": st.column_config.NumberColumn("Прочие на ед., ₽", min_value=0.0, step=10.0, format="%.2f"),
                    "exchange_rate": st.column_config.NumberColumn("Курс", disabled=True, format="%.4f"),
                    "unit_price": st.column_config.NumberColumn("Итог за ед., ₽ (после сохранения)", disabled=True, format="%.2f"),
                    "note": st.column_config.TextColumn("Примечание"),
                },
            ) if raw_rows or selected_materials else pd.DataFrame()
            if not selected_materials:
                st.info("Выберите материал или введите новый цвет.")
        else:
            catalog = read_table("products_catalog")
            production_cfg = read_table("production_settings")
            own_nm = set(pd.to_numeric(production_cfg.loc[pd.to_numeric(production_cfg.get("enabled", 0), errors="coerce").fillna(0).eq(1), "nm_id"], errors="coerce").dropna().astype(int).tolist()) if not production_cfg.empty else set()
            catalog_options: dict[str, dict] = {}
            if not catalog.empty:
                for _, row in catalog.iterrows():
                    nm_id = int(row.get("nm_id", 0) or 0)
                    if nm_id in own_nm:
                        continue
                    label = f"{row.get('supplier_article','')} — {row.get('product_name','')}"
                    catalog_options[label] = row.to_dict()
            recommended_labels = []
            if not product_recommendations.empty:
                rec_articles = set(product_recommendations["Артикул продавца"].astype(str))
                recommended_labels = [label for label, row in catalog_options.items() if str(row.get("supplier_article", "")) in rec_articles]
            selected_products = st.multiselect(
                "Закупаемые товары", list(catalog_options), default=recommended_labels, key="new_proc_products"
            )
            rec_by_nm = product_recommendations.set_index("Артикул WB").to_dict("index") if not product_recommendations.empty else {}
            product_rows = []
            for label in selected_products:
                row = catalog_options[label]
                nm_id = int(row.get("nm_id", 0) or 0)
                rec = rec_by_nm.get(nm_id, {})
                product_rows.append({
                    "nm_id": nm_id, "supplier_article": str(row.get("supplier_article", "") or ""),
                    "product_name": str(row.get("product_name", "") or ""),
                    "quantity": max(1, int(rec.get("Ориентировочно заказать, шт.", 1) or 1)),
                    "supplier_unit_price": 0.0, "delivery_unit_foreign": 0.0,
                    "extra_unit_rub": 0.0, "exchange_rate": float(new_exchange_rate),
                    "unit_price": 0.0, "note": "",
                })
            new_items_df = st.data_editor(
                pd.DataFrame(product_rows), hide_index=True, use_container_width=True,
                key="new_product_proc_items",
                column_config={
                    "nm_id": st.column_config.NumberColumn("Артикул WB", disabled=True, format="%d"),
                    "supplier_article": st.column_config.TextColumn("Артикул продавца", disabled=True),
                    "product_name": st.column_config.TextColumn("Товар", disabled=True),
                    "quantity": st.column_config.NumberColumn("Количество, шт.", min_value=0, step=1, format="%d"),
                    "supplier_unit_price": st.column_config.NumberColumn(f"Цена поставщика, {new_currency}", min_value=0.0, step=1.0, format="%.4f"),
                    "delivery_unit_foreign": st.column_config.NumberColumn(f"Доставка на шт., {new_currency}", min_value=0.0, step=1.0, format="%.4f"),
                    "extra_unit_rub": st.column_config.NumberColumn("Прочие на шт., ₽", min_value=0.0, step=10.0, format="%.2f"),
                    "exchange_rate": st.column_config.NumberColumn("Курс", disabled=True, format="%.4f"),
                    "unit_price": st.column_config.NumberColumn("Итог за шт., ₽ (после сохранения)", disabled=True, format="%.2f"),
                    "note": st.column_config.TextColumn("Примечание"),
                },
            ) if product_rows else pd.DataFrame()
            if not selected_products:
                st.info("Выберите один или несколько закупаемых товаров.")

        if st.button("Сохранить заявку на закупку", type="primary", use_container_width=True, key="save_new_procurement"):
            try:
                order_id = create_procurement_order(
                    procurement_type_ui, new_supplier, new_status, new_order_date, new_payment_date,
                    new_expected_date, new_note, new_items_df,
                    currency=new_currency, exchange_rate=float(new_exchange_rate),
                )
                st.session_state["procurement_flash"] = ("success", f"Закупка №{order_id} сохранена.")
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    with procurement_tabs[2]:
        if procurement_orders.empty:
            st.info("Заявок пока нет. Создайте первую закупку или сформируйте её из рекомендаций.")
        else:
            orders_view = procurement_orders.copy()
            orders_view["Сумма, ₽"] = pd.to_numeric(orders_view["total_amount"], errors="coerce").fillna(0)
            orders_view["Оплачено, ₽"] = pd.to_numeric(orders_view.get("paid_amount", 0), errors="coerce").fillna(0)
            orders_view["К оплате, ₽"] = pd.to_numeric(orders_view.get("outstanding_amount", 0), errors="coerce").fillna(0)
            orders_view["Получено"] = pd.to_numeric(orders_view["posted_total"], errors="coerce").fillna(0)
            orders_view["Ожидается"] = pd.to_datetime(orders_view["expected_date"], errors="coerce")
            st.dataframe(
                orders_view[["order_number", "procurement_type", "supplier_name", "status", "Ожидается", "item_count", "Сумма, ₽", "Оплачено, ₽", "К оплате, ₽", "Получено"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "order_number": "№ заявки", "procurement_type": "Тип", "supplier_name": "Поставщик",
                    "status": "Статус", "Ожидается": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "item_count": st.column_config.NumberColumn("Позиций", format="%d"),
                    "Сумма, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Оплачено, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "К оплате, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Получено": st.column_config.NumberColumn(format="%.2f"),
                },
            )
            order_labels = {int(row["id"]): f"{row['order_number']} · {row['procurement_type']} · {row['status']} · {row['supplier_name'] or 'поставщик не указан'}" for _, row in procurement_orders.iterrows()}
            selected_order_id = st.selectbox("Открыть заявку", list(order_labels), format_func=lambda x: order_labels[int(x)], key="selected_proc_order")
            selected_order = procurement_orders[procurement_orders["id"].eq(int(selected_order_id))].iloc[0]
            selected_items = read_procurement_items(int(selected_order_id))
            order_money_cols = st.columns(3)
            with order_money_cols[0]: kpi_card("Сумма заявки", money(float(selected_order.get("total_amount", 0) or 0)), "Полная стоимость с доставкой")
            with order_money_cols[1]: kpi_card("Оплачено", money(float(selected_order.get("paid_amount", 0) or 0)), f"Платежей: {int(selected_order.get('payment_count',0) or 0)}")
            with order_money_cols[2]: kpi_card("Осталось оплатить", money(float(selected_order.get("outstanding_amount", 0) or 0)), str(selected_order.get("payment_due_date", "") or "Срок не задан"))
            cost_breakdown_cols = st.columns(3)
            with cost_breakdown_cols[0]: kpi_card("Цена поставщика", money(float(selected_order.get("supplier_amount_rub", 0) or 0)), f"{selected_order.get('currency','RUB')} × курс {float(selected_order.get('exchange_rate',1) or 1):.4f}")
            with cost_breakdown_cols[1]: kpi_card("Доставка", money(float(selected_order.get("delivery_amount_rub", 0) or 0)), "В составе полной себестоимости")
            with cost_breakdown_cols[2]: kpi_card("Прочие расходы", money(float(selected_order.get("extra_amount_rub", 0) or 0)), "На всю заявку")
            edit_cols = st.columns(3)
            with edit_cols[0]:
                edit_supplier_options = {int(r["id"]): str(r["name"]) for _, r in suppliers.iterrows()} if not suppliers.empty else {}
                current_supplier_id = int(selected_order.get("supplier_id", 0) or 0)
                edit_supplier_values = [0] + list(edit_supplier_options)
                if current_supplier_id not in edit_supplier_values and current_supplier_id > 0:
                    edit_supplier_values.append(current_supplier_id)
                chosen_edit_supplier_id = st.selectbox(
                    "Поставщик из справочника", edit_supplier_values,
                    index=edit_supplier_values.index(current_supplier_id) if current_supplier_id in edit_supplier_values else 0,
                    format_func=lambda value: "— свободное название —" if int(value)==0 else edit_supplier_options.get(int(value), str(selected_order.get("supplier_name", "") or "Поставщик")),
                    key=f"edit_supplier_id_{selected_order_id}",
                )
                edit_supplier_custom = st.text_input(
                    "Название поставщика", value="" if int(chosen_edit_supplier_id or 0)>0 else str(selected_order.get("supplier_name", "") or ""),
                    key=f"edit_supplier_{selected_order_id}"
                )
                edit_supplier = edit_supplier_options.get(int(chosen_edit_supplier_id or 0), "") or edit_supplier_custom.strip()
                edit_status = st.selectbox("Статус", list(PROCUREMENT_STATUSES), index=list(PROCUREMENT_STATUSES).index(str(selected_order.get("status", "Запланировано"))) if str(selected_order.get("status", "")) in PROCUREMENT_STATUSES else 0, key=f"edit_status_{selected_order_id}")
            with edit_cols[1]:
                edit_order_date = st.date_input("Дата заявки", value=pd.to_datetime(selected_order.get("order_date"), errors="coerce").date() if pd.notna(pd.to_datetime(selected_order.get("order_date"), errors="coerce")) else today_proc, format="DD.MM.YYYY", key=f"edit_order_date_{selected_order_id}")
                edit_payment_date = st.date_input("Оплатить до", value=pd.to_datetime(selected_order.get("payment_due_date"), errors="coerce").date() if pd.notna(pd.to_datetime(selected_order.get("payment_due_date"), errors="coerce")) else today_proc, format="DD.MM.YYYY", key=f"edit_payment_date_{selected_order_id}")
            with edit_cols[2]:
                edit_expected_date = st.date_input("Ожидаемая поставка", value=pd.to_datetime(selected_order.get("expected_date"), errors="coerce").date() if pd.notna(pd.to_datetime(selected_order.get("expected_date"), errors="coerce")) else today_proc, format="DD.MM.YYYY", key=f"edit_expected_date_{selected_order_id}")
                edit_note = st.text_input("Примечание", value=str(selected_order.get("note", "") or ""), key=f"edit_note_{selected_order_id}")
            pricing_cols = st.columns(2)
            currency_options = ["RUB", "USD", "CNY", "EUR"]
            selected_currency = str(selected_order.get("currency", "RUB") or "RUB").upper()
            if selected_currency not in currency_options:
                selected_currency = "RUB"
            with pricing_cols[0]:
                edit_currency = st.selectbox(
                    "Валюта цены поставщика", currency_options,
                    index=currency_options.index(selected_currency), key=f"edit_currency_{selected_order_id}"
                )
            with pricing_cols[1]:
                edit_exchange_rate = st.number_input(
                    "Курс, ₽ за 1 ед. валюты", min_value=0.0001,
                    value=float(selected_order.get("exchange_rate", 1) or 1), step=0.1, format="%.4f",
                    key=f"edit_exchange_rate_{selected_order_id}"
                )
            st.caption("Итоговая себестоимость единицы = (цена поставщика + доставка в валюте) × курс + прочие расходы в рублях. Итог пересчитается после сохранения.")
            if not selected_items.empty:
                item_columns = ["id", "material_name", "supplier_article", "product_name", "quantity", "unit", "roll_length", "supplier_unit_price", "delivery_unit_foreign", "extra_unit_rub", "unit_price", "posted_quantity", "note"]
                for column, default_value in {"supplier_unit_price":0.0, "delivery_unit_foreign":0.0, "extra_unit_rub":0.0}.items():
                    if column not in selected_items.columns:
                        selected_items[column] = default_value
                item_edit = selected_items[item_columns].copy()
                item_edit["Цена сырья, ₽"] = pd.to_numeric(item_edit["supplier_unit_price"], errors="coerce").fillna(0) * float(edit_exchange_rate)
                item_edit["Доставка, ₽"] = pd.to_numeric(item_edit["delivery_unit_foreign"], errors="coerce").fillna(0) * float(edit_exchange_rate)
                item_edit["Цена за метр, ₽"] = item_edit.apply(
                    lambda r: float(r.get("unit_price",0) or 0) / max(0.0001, float(r.get("roll_length",0) or 0)) if str(r.get("unit","")) == "рулон" else 0.0,
                    axis=1,
                )
                item_edit = st.data_editor(
                    item_edit, hide_index=True, use_container_width=True, key=f"edit_proc_items_{selected_order_id}",
                    column_config={
                        "id": st.column_config.NumberColumn("ID", disabled=True, format="%d"),
                        "material_name": st.column_config.TextColumn("Материал", disabled=True),
                        "supplier_article": st.column_config.TextColumn("Артикул", disabled=True),
                        "product_name": st.column_config.TextColumn("Товар", disabled=True),
                        "quantity": st.column_config.NumberColumn("Заказать", min_value=0.0, step=1.0),
                        "unit": st.column_config.TextColumn("Ед.", disabled=True),
                        "roll_length": st.column_config.NumberColumn(
                            "Размер упаковки", min_value=0.1, step=0.1, format="%.2f",
                            help="Используется только при единице «рулон».",
                        ),
                        "supplier_unit_price": st.column_config.NumberColumn(f"Цена поставщика, {edit_currency}", min_value=0.0, step=1.0, format="%.4f"),
                        "delivery_unit_foreign": st.column_config.NumberColumn(f"Доставка на ед., {edit_currency}", min_value=0.0, step=1.0, format="%.4f"),
                        "extra_unit_rub": st.column_config.NumberColumn("Прочие на ед., ₽", min_value=0.0, step=10.0, format="%.2f"),
                        "Цена сырья, ₽": st.column_config.NumberColumn(disabled=True, format="%.2f"),
                        "Доставка, ₽": st.column_config.NumberColumn(disabled=True, format="%.2f"),
                        "unit_price": st.column_config.NumberColumn("Итог за ед., ₽", disabled=True, format="%.2f"),
                        "Цена за метр, ₽": st.column_config.NumberColumn(disabled=True, format="%.2f"),
                        "posted_quantity": st.column_config.NumberColumn("Оприходовано", disabled=True, format="%.2f"),
                        "note": st.column_config.TextColumn("Примечание"),
                    },
                )
                if str(selected_order.get("procurement_type", "")) == "Сырьё":
                    with st.expander("Калькулятор рулона: цена за ярд + доставка по весу", expanded=False):
                        helper_cols = st.columns(4)
                        with helper_cols[0]:
                            helper_yard_price = st.number_input("Цена, $/ярд", min_value=0.0, value=3.0, step=0.1, key=f"yard_price_{selected_order_id}")
                        with helper_cols[1]:
                            helper_weight = st.number_input("Вес рулона, кг", min_value=0.0, value=49.0, step=0.5, key=f"roll_weight_{selected_order_id}")
                        with helper_cols[2]:
                            helper_freight = st.number_input("Доставка, $/кг", min_value=0.0, value=1.1, step=0.1, key=f"freight_kg_{selected_order_id}")
                        with helper_cols[3]:
                            helper_rate = st.number_input("Курс $/₽", min_value=0.0001, value=float(edit_exchange_rate if edit_currency == "USD" else 80.0), step=0.1, key=f"helper_rate_{selected_order_id}")
                        typical_roll = float(pd.to_numeric(selected_items.get("roll_length", pd.Series([25.5])), errors="coerce").dropna().iloc[0]) if not selected_items.empty else 25.5
                        helper_supplier_usd = typical_roll / 0.9144 * float(helper_yard_price)
                        helper_delivery_usd = float(helper_weight) * float(helper_freight)
                        helper_landed = (helper_supplier_usd + helper_delivery_usd) * float(helper_rate)
                        st.info(
                            f"Для рулона {typical_roll:.1f} м: сырьё {helper_supplier_usd:.2f} $, доставка {helper_delivery_usd:.2f} $, "
                            f"итого {helper_landed:,.2f} ₽, или {helper_landed/typical_roll:,.2f} ₽/м.".replace(",", " "),
                        )
                        if st.button("Применить этот расчёт ко всем рулонам заявки", key=f"apply_roll_calc_{selected_order_id}"):
                            helper_df = selected_items.copy()
                            helper_df["supplier_unit_price"] = pd.to_numeric(helper_df["roll_length"], errors="coerce").fillna(25.5) / 0.9144 * float(helper_yard_price)
                            helper_df["delivery_unit_foreign"] = float(helper_delivery_usd)
                            helper_df["extra_unit_rub"] = pd.to_numeric(helper_df.get("extra_unit_rub", 0), errors="coerce").fillna(0)
                            helper_df["exchange_rate"] = float(helper_rate)
                            update_procurement_items(helper_df)
                            update_procurement_order(int(selected_order_id), edit_supplier, edit_status, edit_order_date, edit_payment_date, edit_expected_date, edit_note, currency="USD", exchange_rate=float(helper_rate))
                            refresh_auto_costs()
                            st.session_state["procurement_flash"] = ("success", "Цена рулонов разделена на сырьё и доставку; итоговая себестоимость пересчитана.")
                            st.cache_data.clear(); st.rerun()
            action_cols = st.columns([2, 1])
            with action_cols[0]:
                if st.button("Сохранить изменения заявки", type="primary", use_container_width=True, key=f"save_proc_order_{selected_order_id}"):
                    try:
                        if not selected_items.empty:
                            item_edit["exchange_rate"] = float(edit_exchange_rate)
                            update_procurement_items(item_edit)
                        update_procurement_order(
                            int(selected_order_id), edit_supplier, edit_status, edit_order_date,
                            edit_payment_date, edit_expected_date, edit_note,
                            currency=edit_currency, exchange_rate=float(edit_exchange_rate),
                        )
                        refresh_auto_costs()
                        st.session_state["procurement_flash"] = ("success", "Заявка, цена сырья, доставка и итоговая себестоимость сохранены.")
                        st.cache_data.clear(); st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
            with action_cols[1]:
                confirm_delete = st.checkbox("Подтвердить удаление", key=f"confirm_delete_proc_{selected_order_id}")
                if st.button("Удалить заявку", disabled=not confirm_delete, use_container_width=True, key=f"delete_proc_{selected_order_id}"):
                    try:
                        delete_procurement_order(int(selected_order_id))
                        st.session_state["procurement_flash"] = ("success", "Заявка удалена.")
                        st.cache_data.clear(); st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    with procurement_tabs[3]:
        receivable = procurement_orders[~procurement_orders["status"].isin(["Получено", "Отменено"])].copy() if not procurement_orders.empty else pd.DataFrame()
        if receivable.empty:
            st.info("Нет закупок, ожидающих приёмку.")
        else:
            receive_labels = {int(row["id"]): f"{row['order_number']} · {row['supplier_name'] or 'поставщик не указан'} · {row['status']}" for _, row in receivable.iterrows()}
            receipt_order_id = st.selectbox("Закупка для приёмки", list(receive_labels), format_func=lambda x: receive_labels[int(x)], key="receipt_proc_order")
            receipt_items = read_procurement_items(int(receipt_order_id))
            receipt_items["Осталось"] = (pd.to_numeric(receipt_items["quantity"], errors="coerce").fillna(0) - pd.to_numeric(receipt_items["posted_quantity"], errors="coerce").fillna(0)).clip(lower=0)
            receipt_items["receive_now"] = 0.0
            receipt_editor = st.data_editor(
                receipt_items[["id", "material_name", "supplier_article", "product_name", "unit", "quantity", "posted_quantity", "Осталось", "receive_now"]],
                hide_index=True, use_container_width=True, key=f"receipt_editor_{receipt_order_id}",
                column_config={
                    "id": st.column_config.NumberColumn("ID", disabled=True, format="%d"),
                    "material_name": st.column_config.TextColumn("Материал", disabled=True),
                    "supplier_article": st.column_config.TextColumn("Артикул", disabled=True),
                    "product_name": st.column_config.TextColumn("Товар", disabled=True),
                    "unit": st.column_config.TextColumn("Ед.", disabled=True),
                    "quantity": st.column_config.NumberColumn("Заказано", disabled=True, format="%.2f"),
                    "posted_quantity": st.column_config.NumberColumn("Уже оприходовано", disabled=True, format="%.2f"),
                    "Осталось": st.column_config.NumberColumn(disabled=True, format="%.2f"),
                    "receive_now": st.column_config.NumberColumn("Получено сейчас", min_value=0.0, step=1.0, format="%.2f"),
                },
            )
            confirm_receipt = st.checkbox("Подтверждаю фактическое получение и оприходование", key=f"confirm_proc_receipt_{receipt_order_id}")
            if st.button("Оприходовать поступление", type="primary", disabled=not confirm_receipt, use_container_width=True, key=f"post_proc_receipt_{receipt_order_id}"):
                result = post_procurement_receipt(int(receipt_order_id), receipt_editor.rename(columns={"id": "item_id"})[["item_id", "receive_now"]])
                if result.get("errors"):
                    st.error("; ".join(result["errors"]))
                if result.get("items", 0):
                    text = f"Оприходовано позиций: {result['items']}"
                    if result.get("materials_m", 0): text += f", сырья: {result['materials_m']:.1f} м"
                    if result.get("products", 0): text += f", товаров: {result['products']} шт."
                    st.session_state["procurement_flash"] = ("success", text + ". Производственный план будет пересчитан при открытии раздела «Производство».")
                    st.cache_data.clear(); st.rerun()

    with procurement_tabs[4]:
        st.markdown("#### Платёжный календарь")
        if procurement_orders.empty:
            st.info("Платёжный план появится после создания закупок.")
        else:
            payment_view = procurement_orders.copy()
            payment_view["Оплатить до"] = pd.to_datetime(payment_view["payment_due_date"], errors="coerce")
            payment_view["Сумма, ₽"] = pd.to_numeric(payment_view["total_amount"], errors="coerce").fillna(0)
            payment_view["Оплачено, ₽"] = pd.to_numeric(payment_view.get("paid_amount", 0), errors="coerce").fillna(0)
            payment_view["К оплате, ₽"] = pd.to_numeric(payment_view.get("outstanding_amount", 0), errors="coerce").fillna(0)
            payment_view["Просрочка"] = payment_view.apply(
                lambda r: "Да" if (float(r["К оплате, ₽"]) > 0.01 and pd.notna(r["Оплатить до"]) and r["Оплатить до"].date() < today_proc) else "", axis=1
            )
            st.dataframe(
                payment_view[["order_number", "supplier_name", "status", "Оплатить до", "Сумма, ₽", "Оплачено, ₽", "К оплате, ₽", "Просрочка"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "order_number": "№ заявки", "supplier_name": "Поставщик", "status": "Статус",
                    "Оплатить до": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "Сумма, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "Оплачено, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "К оплате, ₽": st.column_config.NumberColumn(format="%.2f"),
                },
            )
            payable = procurement_orders[
                (~procurement_orders["status"].isin(["Получено", "Отменено"])) &
                pd.to_numeric(procurement_orders.get("outstanding_amount", 0), errors="coerce").fillna(0).gt(0.01)
            ].copy()
            if payable.empty:
                st.success("По активным заявкам задолженности нет.")
            else:
                payable_labels = {
                    int(row["id"]): f"{row['order_number']} · {row['supplier_name'] or 'поставщик не указан'} · осталось {money(float(row['outstanding_amount'] or 0))}"
                    for _, row in payable.iterrows()
                }
                pay_order_id = st.selectbox("Заявка для оплаты", list(payable_labels), format_func=lambda x: payable_labels[int(x)], key="pay_proc_order_v40")
                pay_row = payable[payable["id"].eq(int(pay_order_id))].iloc[0]
                pay_cols = st.columns(4)
                with pay_cols[0]:
                    pay_amount = st.number_input(
                        "Сумма платежа, ₽", min_value=0.01,
                        max_value=max(0.01, float(pay_row.get("outstanding_amount", 0) or 0)),
                        value=max(0.01, float(pay_row.get("outstanding_amount", 0) or 0)), step=100.0,
                        key=f"proc_payment_amount_{pay_order_id}"
                    )
                with pay_cols[1]:
                    pay_date = st.date_input("Дата платежа", value=today_proc, format="DD.MM.YYYY", key=f"proc_payment_date_{pay_order_id}")
                with pay_cols[2]:
                    pay_method = st.selectbox("Способ", list(PAYMENT_METHODS), key=f"proc_payment_method_{pay_order_id}")
                with pay_cols[3]:
                    pay_note = st.text_input("Примечание", key=f"proc_payment_note_{pay_order_id}")
                confirm_pay = st.checkbox("Подтверждаю фактическую оплату поставщику", key=f"confirm_proc_payment_{pay_order_id}")
                if st.button("Провести платёж", disabled=not confirm_pay, type="primary", use_container_width=True, key=f"record_proc_payment_{pay_order_id}"):
                    try:
                        payment_id = record_procurement_payment(int(pay_order_id), float(pay_amount), pay_date, pay_method, pay_note)
                        st.session_state["procurement_flash"] = ("success", f"Платёж №{payment_id} на {money(float(pay_amount))} проведён.")
                        st.cache_data.clear(); st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

        st.markdown("#### История платежей")
        payment_history = read_procurement_payments()
        if payment_history.empty:
            st.info("Платежей пока нет.")
        else:
            payment_history = payment_history.copy()
            payment_history["Дата"] = pd.to_datetime(payment_history["payment_date"], errors="coerce")
            payment_history["Сумма, ₽"] = pd.to_numeric(payment_history["amount"], errors="coerce").fillna(0)
            payment_history["Состояние"] = payment_history["status"].map({"applied":"Проведён", "reversed":"Отменён"}).fillna(payment_history["status"])
            st.dataframe(
                payment_history[["id", "Дата", "order_number", "supplier_name", "Сумма, ₽", "method", "Состояние", "note"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "id": st.column_config.NumberColumn("№ платежа", format="%d"),
                    "Дата": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "order_number": "№ заявки", "supplier_name": "Поставщик",
                    "Сумма, ₽": st.column_config.NumberColumn(format="%.2f"),
                    "method": "Способ", "note": "Примечание",
                },
            )
            active_payments = payment_history[payment_history["status"].eq("applied")].copy()
            if not active_payments.empty:
                payment_labels = {
                    int(r["id"]): f"№{int(r['id'])} · {r['order_number']} · {money(float(r['amount'] or 0))}"
                    for _, r in active_payments.iterrows()
                }
                undo_payment_id = st.selectbox("Отменить ошибочный платёж", list(payment_labels), format_func=lambda x: payment_labels[int(x)], key="undo_proc_payment_id")
                confirm_undo_payment = st.checkbox("Подтверждаю отмену платежа", key="confirm_undo_proc_payment")
                if st.button("Отменить выбранный платёж", disabled=not confirm_undo_payment, key="undo_proc_payment"):
                    result = undo_procurement_payment(int(undo_payment_id))
                    st.session_state["procurement_flash"] = ("success" if result.get("ok") else "error", str(result.get("message", "")))
                    st.cache_data.clear(); st.rerun()

    with procurement_tabs[5]:
        st.markdown("#### Справочник поставщиков")
        supplier_summary = read_suppliers()
        if not supplier_summary.empty:
            supplier_view = supplier_summary.copy()
            supplier_view["Закупок, ₽"] = pd.to_numeric(supplier_view.get("total_amount", 0), errors="coerce").fillna(0)
            supplier_view["Оплачено, ₽"] = pd.to_numeric(supplier_view.get("paid_amount", 0), errors="coerce").fillna(0)
            supplier_view["Активен"] = supplier_view["active"].astype(int).eq(1)
            supplier_view["Последняя закупка"] = pd.to_datetime(supplier_view.get("last_order_date"), errors="coerce")
            st.dataframe(
                supplier_view[["name", "contact_person", "phone", "messenger", "country", "default_currency", "lead_time_days", "payment_terms_days", "order_count", "Закупок, ₽", "Оплачено, ₽", "Последняя закупка", "Активен"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "name":"Поставщик", "contact_person":"Контакт", "phone":"Телефон", "messenger":"Мессенджер",
                    "country":"Страна", "default_currency":"Валюта", "lead_time_days":st.column_config.NumberColumn("Срок поставки, дн.",format="%d"),
                    "payment_terms_days":st.column_config.NumberColumn("Срок оплаты, дн.",format="%d"),
                    "order_count":st.column_config.NumberColumn("Заявок",format="%d"),
                    "Закупок, ₽":st.column_config.NumberColumn(format="%.2f"), "Оплачено, ₽":st.column_config.NumberColumn(format="%.2f"),
                    "Последняя закупка":st.column_config.DateColumn(format="DD.MM.YYYY"), "Активен":st.column_config.CheckboxColumn(disabled=True),
                },
            )
        supplier_choice_map = {int(r["id"]): str(r["name"]) for _, r in supplier_summary.iterrows()} if not supplier_summary.empty else {}
        supplier_edit_values = [0] + list(supplier_choice_map)
        supplier_edit_id = st.selectbox(
            "Карточка поставщика", supplier_edit_values,
            format_func=lambda value: "＋ Новый поставщик" if int(value)==0 else supplier_choice_map[int(value)],
            key="supplier_directory_choice"
        )
        supplier_current = supplier_defaults(int(supplier_edit_id)) if int(supplier_edit_id or 0)>0 else {}
        sup_cols = st.columns(3)
        with sup_cols[0]:
            sup_name = st.text_input("Название", value=str(supplier_current.get("name", "") or ""), key=f"sup_name_{supplier_edit_id}")
            sup_contact = st.text_input("Контактное лицо", value=str(supplier_current.get("contact_person", "") or ""), key=f"sup_contact_{supplier_edit_id}")
            sup_phone = st.text_input("Телефон", value=str(supplier_current.get("phone", "") or ""), key=f"sup_phone_{supplier_edit_id}")
        with sup_cols[1]:
            sup_messenger = st.text_input("Telegram / WeChat / WhatsApp", value=str(supplier_current.get("messenger", "") or ""), key=f"sup_messenger_{supplier_edit_id}")
            sup_email = st.text_input("Email", value=str(supplier_current.get("email", "") or ""), key=f"sup_email_{supplier_edit_id}")
            sup_country = st.text_input("Страна / город", value=str(supplier_current.get("country", "") or ""), key=f"sup_country_{supplier_edit_id}")
            supplier_currency_options = ["RUB", "USD", "CNY", "EUR"]
            current_supplier_currency = str(supplier_current.get("default_currency", "RUB") or "RUB").upper()
            if current_supplier_currency not in supplier_currency_options:
                current_supplier_currency = "RUB"
            sup_currency = st.selectbox("Валюта поставщика", supplier_currency_options, index=supplier_currency_options.index(current_supplier_currency), key=f"sup_currency_{supplier_edit_id}")
        with sup_cols[2]:
            sup_payment_terms = st.number_input("Оплата через, дней", min_value=0, max_value=365, value=int(supplier_current.get("payment_terms_days", 3) or 0), step=1, key=f"sup_payment_terms_{supplier_edit_id}")
            sup_lead_time = st.number_input("Обычный срок поставки, дней", min_value=0, max_value=365, value=int(supplier_current.get("lead_time_days", 7) or 0), step=1, key=f"sup_lead_{supplier_edit_id}")
            sup_active = st.checkbox("Активный поставщик", value=bool(int(supplier_current.get("active", 1) or 0)), key=f"sup_active_{supplier_edit_id}")
        sup_note = st.text_input("Примечание к поставщику", value=str(supplier_current.get("note", "") or ""), key=f"sup_note_{supplier_edit_id}")
        if st.button("Сохранить поставщика", type="primary", use_container_width=True, key=f"save_supplier_{supplier_edit_id}"):
            try:
                saved_id = save_supplier(
                    int(supplier_edit_id) if int(supplier_edit_id or 0)>0 else None,
                    sup_name, sup_contact, sup_phone, sup_messenger, sup_email, sup_country,
                    sup_currency, int(sup_payment_terms), int(sup_lead_time), bool(sup_active), sup_note,
                )
                st.session_state["procurement_flash"] = ("success", f"Поставщик сохранён, ID {saved_id}.")
                st.cache_data.clear(); st.rerun()
            except Exception as exc:
                st.error(str(exc))

    with procurement_tabs[6]:
        movements = read_procurement_movements(500)
        if movements.empty:
            st.info("Поступлений по закупкам пока не проводилось.")
        else:
            movements_view = movements.copy()
            movements_view["Дата"] = pd.to_datetime(movements_view["movement_date"], errors="coerce")
            movements_view["Операция"] = movements_view["movement_type"].map({
                "procurement_material_receipt": "Приёмка сырья",
                "procurement_product_receipt": "Приёмка товара",
                "reversal": "Отмена",
            }).fillna(movements_view["movement_type"])
            movements_view["Состояние"] = movements_view.apply(lambda r: "Отменена" if pd.notna(r.get("reversed_at")) else "Проведена", axis=1)
            st.dataframe(
                movements_view[["id", "Дата", "Операция", "Состояние", "procurement_order_id", "supplier_article", "product_name", "material_name", "procurement_quantity", "material_delta", "ready_delta", "note"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "id": st.column_config.NumberColumn("№ движения", format="%d"),
                    "Дата": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "procurement_order_id": st.column_config.NumberColumn("ID закупки", format="%d"),
                    "supplier_article": "Артикул", "product_name": "Товар", "material_name": "Материал",
                    "procurement_quantity": st.column_config.NumberColumn("Принято, ед.", format="%.2f"),
                    "material_delta": st.column_config.NumberColumn("Изменение сырья, м", format="%.2f"),
                    "ready_delta": st.column_config.NumberColumn("Изменение готового, шт.", format="%d"),
                    "note": "Примечание",
                },
            )
            cancellable = movements[movements["reversed_at"].isna() & movements["movement_type"].isin(["procurement_material_receipt", "procurement_product_receipt"])].copy()
            if not cancellable.empty:
                labels = {int(r["id"]): f"№{int(r['id'])} · {r.get('material_name') or r.get('supplier_article')} · {float(r.get('procurement_quantity',0) or 0):g}" for _, r in cancellable.iterrows()}
                undo_id = st.selectbox("Отменить ошибочную приёмку", list(labels), format_func=lambda x: labels[int(x)], key="undo_proc_movement")
                confirm_undo = st.checkbox("Подтверждаю отмену поступления", key="confirm_undo_procurement")
                if st.button("Отменить выбранную приёмку", disabled=not confirm_undo, key="undo_procurement_receipt"):
                    result = undo_inventory_movement(int(undo_id))
                    st.session_state["procurement_flash"] = ("success" if result.get("ok") else "error", str(result.get("message", "")))
                    st.cache_data.clear(); st.rerun()

    with procurement_tabs[7]:
        st.markdown("#### Консолидированный план закупок по поставщикам")
        st.caption(
            "План объединяет прибыльные закупаемые SKU одного поставщика в одну заявку. "
            "Из потребности вычитаются остаток WB, подтверждённый готовый товар, товар в пути и ещё не полученные закупки. "
            "Убыточные карточки блокируются и не попадают в создаваемые заявки."
        )
        plan_settings = load_settings()
        plan_purchase_rules = plan_settings.get("decision_purchase_rules", {})
        if not isinstance(plan_purchase_rules, dict):
            plan_purchase_rules = {}
        plan_safety_days = float(plan_settings.get("decision_stock_days", 14.0) or 14.0)
        plan_target_days = float(plan_settings.get("decision_purchase_target_days", 30.0) or 30.0)
        plan_default_moq = int(plan_settings.get("decision_purchase_default_moq", 10) or 10)
        plan_default_lead = float(plan_settings.get("decision_purchase_default_lead_days", 14.0) or 14.0)
        purchase_item_plan, supplier_plan = build_consolidated_purchase_plan(
            data.products, data.financial_products, procurement_orders, procurement_items_all,
            suppliers, plan_purchase_rules, safety_days=plan_safety_days,
            default_target_days=plan_target_days, default_moq=plan_default_moq,
            default_lead_days=plan_default_lead,
        )

        budget_cols = st.columns([1, 1, 2])
        with budget_cols[0]:
            available_budget = st.number_input(
                "Доступный бюджет закупок, ₽", min_value=0.0, step=10000.0,
                value=float(plan_settings.get("procurement_available_budget_rub", 0.0) or 0.0),
                key="procurement_available_budget_rub"
            )
        with budget_cols[1]:
            if st.button("Сохранить бюджет", key="save_procurement_available_budget"):
                budget_settings = load_settings()
                budget_settings["procurement_available_budget_rub"] = float(available_budget)
                save_settings(budget_settings)
                st.success("Бюджет закупок сохранён.")
        with budget_cols[2]:
            st.caption("Ноль означает, что лимит оборотных средств не задан. Бюджет используется только для контроля и не проводит платежи.")

        total_plan_amount = float(supplier_plan["Сумма заказа, ₽"].sum()) if not supplier_plan.empty else 0.0
        total_plan_units = float(supplier_plan["Единиц"].sum()) if not supplier_plan.empty else 0.0
        total_plan_contribution = float(supplier_plan["Сохраняемая маржинальная прибыль, ₽"].sum()) if not supplier_plan.empty else 0.0
        budget_gap = max(0.0, total_plan_amount - float(available_budget)) if float(available_budget) > 0 else 0.0
        unassigned_count = int(purchase_item_plan["Статус плана"].eq("Назначить поставщика").sum()) if not purchase_item_plan.empty else 0
        blocked_count = int(purchase_item_plan["Статус плана"].eq("Пауза: убыточно").sum()) if not purchase_item_plan.empty else 0
        plan_cards = st.columns(6)
        with plan_cards[0]: kpi_card("Поставщиков", num(len(supplier_plan)), "С готовыми к заказу позициями")
        with plan_cards[1]: kpi_card("Позиций к заказу", num(int(supplier_plan["Позиций"].sum()) if not supplier_plan.empty else 0), f"{num(total_plan_units)} единиц")
        with plan_cards[2]: kpi_card("Сумма плана", money(total_plan_amount), "По полным закупочным ценам")
        with plan_cards[3]: kpi_card("Кассовый разрыв", money(budget_gap), "0 ₽, если бюджет не задан или достаточен")
        with plan_cards[4]: kpi_card("Защищаемая маржа", money(total_plan_contribution), "Оценка при сохранении текущей экономики")
        with plan_cards[5]: kpi_card("Нужно настроить", num(unassigned_count + blocked_count), f"Поставщик: {unassigned_count}; убыточно: {blocked_count}")

        if budget_gap > 0:
            st.warning(f"Для полного плана не хватает {money(budget_gap)} оборотных средств. Сначала финансируйте критические позиции с ближайшим обнулением.")
        if purchase_item_plan.empty:
            st.success("Закупаемых SKU для планирования нет.")
        else:
            status_counts = purchase_item_plan["Статус плана"].value_counts().to_dict()
            if status_counts.get("Назначить поставщика", 0):
                st.info("Часть потребности не консолидирована: назначьте поставщика в «Финансы → Центр решений → MOQ и сроки закупаемых товаров».")
            price_checks = int(status_counts.get("Указать цену", 0) or 0) + int(status_counts.get("Проверить цену", 0) or 0)
            if price_checks:
                st.info("Для части SKU нет подтверждённой полной цены поставщика. Укажите её в правилах SKU или используйте цену последней проведённой закупки.")
            st.markdown("##### План по позициям")
            st.dataframe(
                purchase_item_plan, hide_index=True, use_container_width=True,
                height=min(680, 110 + 38 * len(purchase_item_plan)),
                column_config={
                    "Артикул WB": st.column_config.NumberColumn(format="%d"),
                    "Остаток WB": st.column_config.NumberColumn(format="%.0f"),
                    "Готово у вас": st.column_config.NumberColumn(format="%.0f"),
                    "В пути": st.column_config.NumberColumn(format="%.0f"),
                    "Открытые закупки": st.column_config.NumberColumn(format="%.0f"),
                    "Продаж/день": st.column_config.NumberColumn(format="%.2f"),
                    "Запас WB, дней": st.column_config.NumberColumn(format="%.1f"),
                    "MOQ": st.column_config.NumberColumn(format="%d"),
                    "Срок поставки, дней": st.column_config.NumberColumn(format="%.0f"),
                    "Целевой горизонт, дней": st.column_config.NumberColumn(format="%.0f"),
                    "Рекомендовано, шт": st.column_config.NumberColumn(format="%d"),
                    "Полная цена, ₽/шт": st.column_config.NumberColumn(format="%.2f ₽"),
                    "Сумма, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                    "Маржинальная прибыль/ед., ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                    "Сохраняемая маржинальная прибыль, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                    "Оплатить до": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "Ожидаемая поставка": st.column_config.DateColumn(format="DD.MM.YYYY"),
                },
            )
            st.download_button(
                "Скачать консолидированный план CSV",
                purchase_item_plan.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"consolidated_procurement_plan_{today_proc:%Y%m%d}.csv",
                mime="text/csv", key="download_consolidated_procurement_plan"
            )

        st.markdown("##### Сводка по поставщикам")
        if supplier_plan.empty:
            st.info("Готовых к созданию заявок пока нет. Проверьте поставщиков, цены, MOQ и прибыльность карточек.")
        else:
            st.dataframe(
                supplier_plan, hide_index=True, use_container_width=True,
                column_config={
                    "Позиций": st.column_config.NumberColumn(format="%d"),
                    "Единиц": st.column_config.NumberColumn(format="%d"),
                    "Сумма заказа, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                    "Сохраняемая маржинальная прибыль, ₽": st.column_config.NumberColumn(format="%.2f ₽"),
                    "Оплатить до": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "Ожидаемая поставка": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "Нулевых цен": st.column_config.NumberColumn(format="%d"),
                    "Критических позиций": st.column_config.NumberColumn(format="%d"),
                },
            )
            supplier_names_for_order = supplier_plan["Поставщик"].astype(str).tolist()
            selected_plan_suppliers = st.multiselect(
                "Создать отдельные заявки для поставщиков", supplier_names_for_order,
                default=supplier_names_for_order, key="consolidated_plan_suppliers"
            )
            selected_lines = purchase_item_plan[
                purchase_item_plan["Поставщик"].isin(selected_plan_suppliers)
                & purchase_item_plan["Статус плана"].eq("К заказу")
                & purchase_item_plan["Рекомендовано, шт"].gt(0)
            ].copy()
            missing_prices = int((selected_lines["Полная цена, ₽/шт"] <= 0).sum()) if not selected_lines.empty else 0
            if missing_prices:
                st.error(f"Нельзя создать заявки: у {missing_prices} выбранных позиций не указана полная закупочная цена.")
            confirm_consolidated = st.checkbox(
                "Подтверждаю создание черновых заявок без проведения оплаты и без изменения остатков",
                key="confirm_consolidated_orders"
            )
            create_disabled = (not selected_plan_suppliers) or selected_lines.empty or missing_prices > 0 or not confirm_consolidated
            if st.button(
                "Создать заявки по выбранным поставщикам", type="primary", use_container_width=True,
                disabled=create_disabled, key="create_consolidated_orders"
            ):
                created: list[str] = []
                errors: list[str] = []
                for supplier_name in selected_plan_suppliers:
                    supplier_lines = selected_lines[selected_lines["Поставщик"].eq(supplier_name)].copy()
                    if supplier_lines.empty:
                        continue
                    supplier_row = suppliers[suppliers["name"].astype(str).str.casefold().eq(str(supplier_name).casefold())]
                    payment_terms = int(float(supplier_row.iloc[0].get("payment_terms_days", 0) or 0)) if not supplier_row.empty else 0
                    expected_date = max(supplier_lines["Ожидаемая поставка"])
                    payment_date = today_proc + timedelta(days=max(0, payment_terms))
                    order_items = pd.DataFrame({
                        "nm_id": supplier_lines["Артикул WB"].astype(int),
                        "supplier_article": supplier_lines["Артикул продавца"].astype(str),
                        "product_name": supplier_lines["Товар"].astype(str),
                        "quantity": supplier_lines["Рекомендовано, шт"].astype(float),
                        "unit_price": supplier_lines["Полная цена, ₽/шт"].astype(float),
                        "supplier_unit_price": 0.0,
                        "delivery_unit_foreign": 0.0,
                        "extra_unit_rub": 0.0,
                        "exchange_rate": 1.0,
                        "note": supplier_lines.apply(
                            lambda row: f"Сводный план: горизонт {float(row['Целевой горизонт, дней']):.0f} дн.; MOQ {int(row['MOQ'])}", axis=1
                        ),
                    })
                    signature_text = "|".join(
                        f"{int(row['Артикул WB'])}:{int(row['Рекомендовано, шт'])}:{float(row['Полная цена, ₽/шт']):.2f}"
                        for _, row in supplier_lines.sort_values("Артикул WB").iterrows()
                    )
                    signature = hashlib.sha1(signature_text.encode("utf-8")).hexdigest()[:12]
                    try:
                        order_id = create_procurement_order(
                            "Товар", supplier_name, "Запланировано", today_proc, payment_date, expected_date,
                            "Создано из консолидированного плана закупок v5.2. Требует подтверждения поставщику и проверки цен.",
                            order_items, source_key=f"consolidated-v52-{today_proc.isoformat()}-{supplier_name.casefold()}-{signature}",
                            currency="RUB", exchange_rate=1.0,
                        )
                        created.append(f"{supplier_name}: заявка ID {order_id}")
                    except Exception as exc:
                        errors.append(f"{supplier_name}: {exc}")
                if created:
                    st.session_state["procurement_flash"] = ("success", "Созданы черновые заявки: " + "; ".join(created))
                if errors:
                    st.session_state["procurement_flash"] = ("error", "Ошибки: " + "; ".join(errors))
                st.cache_data.clear()
                st.rerun()

        st.caption(
            "Сохраняемая маржинальная прибыль — не гарантированный дополнительный доход, а оценка вклада продаж, "
            "который может быть потерян при дефиците. Созданные заявки имеют статус «Запланировано»: они не оплачиваются, "
            "не отправляются поставщику и не меняют остатки автоматически."
        )

