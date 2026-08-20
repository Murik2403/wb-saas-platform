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
from datetime import (
    date,
    timedelta,
)
from db import (
    close_production_shift,
    complete_wip_blank_batch,
    get_production_capacity,
    issue_wip_material,
    post_dispatches,
    post_manual_wip_packaging,
    post_wb_receipts,
    post_wip_blank_batch,
    read_inventory_movements,
    read_production_cost_batches,
    read_table,
    read_wip_blank_allocations,
    read_wip_blank_batches,
    read_wip_blank_summary,
    save_execution_tasks,
    sync_generated_execution_tasks,
    undo_inventory_movement,
    wip_module_status,
)
from html import (
    escape,
)


def render(ctx: dict) -> None:
    data = ctx['data']
    end = ctx['end']
    start = ctx['start']
    today_msk = ctx['today_msk']

    st.markdown("### План производства")
    st.caption(
        "План учитывает остатки на WB, готовые комплекты на производстве, товары в поставках, экономику карточек, "
        "сырьё по цветам и производственную мощность. Скорость продаж рассчитывается по последним 14 дням выбранного периода."
    )

    production_settings = read_table("production_settings")
    enabled_settings = production_settings[
        pd.to_numeric(production_settings.get("enabled", 0), errors="coerce").fillna(0).astype(int) == 1
    ].copy() if not production_settings.empty else production_settings

    if enabled_settings.empty:
        st.info(
            "Товары для производства ещё не выбраны. Откройте «Настройки» → «Параметры производства», "
            "отметьте нужные карточки и сохраните нормы."
        )
    else:
        plan = enabled_settings.rename(columns={
            "nm_id": "Артикул WB",
            "supplier_article": "Артикул продавца",
            "material_per_unit": "Материал на ед., м",
            "target_days": "Целевой запас, дней",
            "min_batch": "Мин. партия, компл.",
            "note": "Примечание",
            "blank_type": "Тип заготовки",
            "pack_size": "Штук в комплекте",
            "material_name": "Материал / цвет",
        }).copy()

        product_cols = [
            "Артикул WB", "Артикул продавца", "Товар", "Остаток",
            "Продаж/день", "Запас, дней"
        ]
        product_base = data.products[product_cols].copy() if not data.products.empty else pd.DataFrame(columns=product_cols)
        plan = plan.merge(product_base, on="Артикул WB", how="left", suffixes=("_настройка", ""))
        if "Артикул продавца_настройка" in plan.columns:
            plan["Артикул продавца"] = plan["Артикул продавца"].fillna("").where(
                plan["Артикул продавца"].fillna("").astype(str).str.len() > 0,
                plan["Артикул продавца_настройка"],
            )
            plan = plan.drop(columns=["Артикул продавца_настройка"], errors="ignore")

        finance_cols = [
            "Артикул WB", "Статус", "Расчётная прибыль", "Расчётная маржа, %",
            "Реклама", "Доля рекламы, %", "Возвраты, %", "Рентабельность затрат, %",
            "Основная причина", "Рекомендация"
        ]
        finance_base = data.financial_products[finance_cols].copy() if not data.financial_products.empty else pd.DataFrame(columns=finance_cols)
        plan = plan.merge(finance_base, on="Артикул WB", how="left")

        pipeline = read_table("product_pipeline")
        if pipeline.empty:
            pipeline = pd.DataFrame(columns=[
                "nm_id", "local_known", "ready_units", "inbound_known", "inbound_units", "inbound_date", "note"
            ])
        pipeline = pipeline.rename(columns={
            "nm_id": "Артикул WB",
            "local_known": "Локальный остаток известен",
            "ready_units": "Готово на производстве",
            "inbound_known": "Поставка указана",
            "inbound_units": "В пути на WB",
            "inbound_date": "Дата прибытия",
            "note": "Примечание поставки",
        })
        pipeline_cols = [
            "Артикул WB", "Локальный остаток известен", "Готово на производстве",
            "Поставка указана", "В пути на WB", "Дата прибытия", "Примечание поставки"
        ]
        for col in pipeline_cols:
            if col not in pipeline.columns:
                pipeline[col] = "" if col in {"Дата прибытия", "Примечание поставки"} else 0
        plan = plan.merge(pipeline[pipeline_cols], on="Артикул WB", how="left")

        numeric_defaults = {
            "Остаток": 0.0, "Продаж/день": 0.0, "Материал на ед., м": 0.0,
            "Целевой запас, дней": 21.0, "Мин. партия, компл.": 1.0,
            "Штук в комплекте": 4.0, "Расчётная прибыль": 0.0,
            "Расчётная маржа, %": 0.0, "Реклама": 0.0,
            "Доля рекламы, %": 0.0, "Возвраты, %": 0.0,
            "Рентабельность затрат, %": 0.0, "Готово на производстве": 0.0,
            "В пути на WB": 0.0,
        }
        for col, default in numeric_defaults.items():
            if col not in plan.columns:
                plan[col] = default
            plan[col] = pd.to_numeric(plan[col], errors="coerce").fillna(default)
        plan["Запас, дней"] = pd.to_numeric(plan.get("Запас, дней"), errors="coerce")
        plan["Локальный остаток известен"] = pd.to_numeric(
            plan.get("Локальный остаток известен", 0), errors="coerce"
        ).fillna(0).astype(int).eq(1)
        plan["Поставка указана"] = pd.to_numeric(
            plan.get("Поставка указана", 0), errors="coerce"
        ).fillna(0).astype(int).eq(1)
        plan["Дата прибытия"] = pd.to_datetime(plan.get("Дата прибытия"), errors="coerce")
        for col in [
            "Товар", "Артикул продавца", "Статус", "Основная причина", "Рекомендация",
            "Примечание", "Тип заготовки", "Материал / цвет", "Примечание поставки"
        ]:
            if col not in plan.columns:
                plan[col] = ""
            plan[col] = plan[col].fillna("").astype(str)

        inferred_plan_materials = plan.apply(
            lambda r: infer_material_name(str(r.get("Артикул продавца", "")), str(r.get("Товар", ""))), axis=1
        )
        plan["Материал / цвет"] = plan["Материал / цвет"].where(
            plan["Материал / цвет"].str.strip().ne(""), inferred_plan_materials
        ).replace("", "Не указан")

        plan["Готово учтено"] = plan["Готово на производстве"].where(plan["Локальный остаток известен"], 0).clip(lower=0)
        plan["В пути учтено"] = plan["В пути на WB"].where(plan["Поставка указана"], 0).clip(lower=0)
        plan["Доступно до поставки"] = plan["Остаток"] + plan["Готово учтено"]
        plan["Всего с поставками"] = plan["Доступно до поставки"] + plan["В пути учтено"]
        plan["Запас с готовым, дней"] = plan.apply(
            lambda r: float(r["Доступно до поставки"]) / float(r["Продаж/день"])
            if float(r["Продаж/день"]) > 0 else float("nan"), axis=1
        )
        plan["Запас с поставками, дней"] = plan.apply(
            lambda r: float(r["Всего с поставками"]) / float(r["Продаж/день"])
            if float(r["Продаж/день"]) > 0 else float("nan"), axis=1
        )
        # Only stock already available on WB delays the moment when buyers see zero.
        # Ready goods at the workshop reduce production demand, but still need delivery to WB.
        plan["Запас WB, дней"] = plan.apply(
            lambda r: float(r["Остаток"]) / float(r["Продаж/день"])
            if float(r["Продаж/день"]) > 0 else float("nan"), axis=1
        )
        plan["Дата обнуления до поставки"] = plan.apply(
            lambda r: pd.Timestamp(today_msk + timedelta(days=max(0, int(math.floor(float(r["Запас WB, дней"]))))))
            if pd.notna(r["Запас WB, дней"]) else pd.NaT, axis=1
        )
        plan["Риск до поставки"] = plan.apply(
            lambda r: bool(r["Поставка указана"] and float(r["В пути учтено"]) > 0
                           and pd.notna(r["Дата прибытия"])
                           and pd.notna(r["Дата обнуления до поставки"])
                           and r["Дата обнуления до поставки"] < r["Дата прибытия"]), axis=1
        )

        plan["Целевой остаток"] = (plan["Продаж/день"] * plan["Целевой запас, дней"]).apply(math.ceil)
        plan["Потребность, компл."] = (plan["Целевой остаток"] - plan["Всего с поставками"]).clip(lower=0)
        plan["Мин. партия, компл."] = plan["Мин. партия, компл."].clip(lower=1).round().astype(int)
        plan["Штук в комплекте"] = plan["Штук в комплекте"].clip(lower=1).round().astype(int)
        plan["План без экономики"] = plan.apply(
            lambda r: int(math.ceil(float(r["Потребность, компл."]) / int(r["Мин. партия, компл."])) * int(r["Мин. партия, компл."]))
            if float(r["Потребность, компл."]) > 0 else 0,
            axis=1,
        )

        def recommended_qty(row: pd.Series) -> int:
            status = str(row.get("Статус", ""))
            raw = int(row.get("План без экономики", 0) or 0)
            batch = int(row.get("Мин. партия, компл.", 1) or 1)
            if status == "Убыточный":
                return 0
            if status in {"Низкая маржа", "Недостаточно данных"} and raw > 0:
                return min(raw, batch)
            return raw

        plan["Рекомендовано, компл."] = plan.apply(recommended_qty, axis=1)
        plan["Штук к производству"] = plan["Рекомендовано, компл."] * plan["Штук в комплекте"]
        plan["Нужно материала, м"] = plan["Рекомендовано, компл."] * plan["Материал на ед., м"]

        def priority(row: pd.Series) -> str:
            status = str(row.get("Статус", ""))
            velocity = float(row.get("Продаж/день", 0) or 0)
            days_ready = row.get("Запас WB, дней")
            qty = int(row.get("Рекомендовано, компл.", 0) or 0)
            if status == "Убыточный":
                return "Стоп"
            if velocity <= 0:
                return "Нет спроса"
            if qty <= 0:
                return "Запас достаточен"
            if bool(row.get("Риск до поставки", False)):
                return "Срочно"
            if pd.notna(days_ready) and float(days_ready) < 7:
                return "Срочно"
            if pd.notna(days_ready) and float(days_ready) < 14:
                return "Высокий"
            return "Средний"

        def production_action(row: pd.Series) -> str:
            status = str(row.get("Статус", ""))
            qty = int(row.get("Рекомендовано, компл.", 0) or 0)
            ready = int(row.get("Готово учтено", 0) or 0)
            inbound = int(row.get("В пути учтено", 0) or 0)
            if status == "Убыточный":
                return "Не производить до исправления экономики"
            if qty <= 0:
                if ready or inbound:
                    return f"Производство не нужно: готово {ready}, в пути {inbound} компл."
                return "Производство пока не требуется"
            if status == "Низкая маржа":
                return f"Только минимальная партия {qty} компл.; проверить цену"
            if status == "Недостаточно данных":
                return f"Тестовая партия {qty} компл."
            if bool(row.get("Риск до поставки", False)):
                return f"Произвести {qty} компл.; поставка придёт после возможного обнуления"
            return f"Произвести {qty} компл."

        plan["Приоритет"] = plan.apply(priority, axis=1)
        plan["Действие"] = plan.apply(production_action, axis=1)
        priority_order = {"Срочно": 0, "Высокий": 1, "Средний": 2, "Стоп": 3, "Нет спроса": 4, "Запас достаточен": 5}
        plan["_priority"] = plan["Приоритет"].map(priority_order).fillna(9)
        plan = plan.sort_values(
            ["_priority", "Запас с готовым, дней", "Тип заготовки", "Материал / цвет", "Расчётная прибыль"],
            ascending=[True, True, True, True, False]
        ).drop(columns=["План без экономики"])

        # Common raw-material stock by color. Demand is broken down by whatever
        # blank types the tenant actually uses (any number of freely-named
        # types, not just two) -- one physical roll of a given color is never
        # counted twice across types.
        material_rows = plan[plan["Нужно материала, м"] > 0].copy()
        blank_type_labels = sorted({
            t for t in material_rows.get("Тип заготовки", pd.Series(dtype=str)).astype(str).str.strip().tolist() if t
        })
        breakdown_columns = [f"{label}, м" for label in blank_type_labels]
        if material_rows.empty or not blank_type_labels:
            material_plan = pd.DataFrame(columns=["Материал / цвет"] + breakdown_columns + ["Потребность, м"])
        else:
            material_breakdown = material_rows.pivot_table(
                index="Материал / цвет", columns="Тип заготовки", values="Нужно материала, м",
                aggfunc="sum", fill_value=0
            ).reset_index()
            for label, column in zip(blank_type_labels, breakdown_columns):
                material_breakdown[column] = pd.to_numeric(material_breakdown.get(label, 0), errors="coerce").fillna(0.0)
            material_plan = material_breakdown[["Материал / цвет"] + breakdown_columns].copy()
            material_plan["Потребность, м"] = material_plan[breakdown_columns].sum(axis=1)

        material_plan["material_key"] = material_plan["Материал / цвет"].apply(material_key)
        inventory = read_table("material_inventory_color")
        if inventory.empty:
            inventory = pd.DataFrame(columns=[
                "material_key", "material_name", "balance_known", "full_rolls",
                "partial_meters", "roll_length", "note", "updated_at"
            ])
        material_plan = material_plan.merge(
            inventory[[
                "material_key", "balance_known", "full_rolls", "partial_meters",
                "roll_length", "note", "updated_at"
            ]], on="material_key", how="left"
        )
        material_plan["Остаток указан"] = pd.to_numeric(material_plan.get("balance_known"), errors="coerce").fillna(0).astype(int).eq(1)
        material_plan["Полных рулонов"] = pd.to_numeric(material_plan.get("full_rolls"), errors="coerce").fillna(0).clip(lower=0).astype(int)
        material_plan["Открытый остаток, м"] = pd.to_numeric(material_plan.get("partial_meters"), errors="coerce").fillna(0).clip(lower=0)
        material_plan["Длина рулона, м"] = pd.to_numeric(material_plan.get("roll_length"), errors="coerce").fillna(25.5).clip(lower=0.1)
        material_plan["На складе, м"] = material_plan["Полных рулонов"] * material_plan["Длина рулона, м"] + material_plan["Открытый остаток, м"]
        material_plan["Покрыто складом, м"] = material_plan[["Потребность, м", "На складе, м"]].min(axis=1)
        material_plan["Не хватает, м"] = (material_plan["Потребность, м"] - material_plan["На складе, м"]).clip(lower=0)
        material_plan["Остаток после плана, м"] = material_plan["На складе, м"] - material_plan["Потребность, м"]

        def rolls_to_use(r: pd.Series) -> int:
            if not bool(r["Остаток указан"]):
                return 0
            need_after_open = max(float(r["Потребность, м"]) - float(r["Открытый остаток, м"]), 0.0)
            return min(int(r["Полных рулонов"]), int(math.ceil(need_after_open / float(r["Длина рулона, м"])))) if need_after_open > 0 else 0

        material_plan["Рулонов использовать"] = material_plan.apply(rolls_to_use, axis=1)
        material_plan["Рулонов докупить"] = material_plan.apply(
            lambda r: int(math.ceil(float(r["Не хватает, м"]) / float(r["Длина рулона, м"])))
            if bool(r["Остаток указан"]) and float(r["Не хватает, м"]) > 0 else 0, axis=1
        )

        def material_status(r: pd.Series) -> str:
            if not bool(r["Остаток указан"]):
                return "Остаток не указан"
            if float(r["Не хватает, м"]) > 0.01:
                return "Не хватает сырья"
            if float(r["Остаток после плана, м"]) < float(r["Длина рулона, м"]) * 0.25:
                return "Запаса почти не останется"
            return "Сырья достаточно"

        material_plan["Статус сырья"] = material_plan.apply(material_status, axis=1)
        for col in ["На складе, м", "Покрыто складом, м", "Не хватает, м", "Остаток после плана, м"]:
            material_plan.loc[~material_plan["Остаток указан"], col] = float("nan")

        # Capacity calendar v3.1. First create a bridge stock for every urgent SKU,
        # then fill the remaining 21-day target. This prevents one fast seller from
        # occupying the whole first day while other cards approach zero.
        capacity = get_production_capacity()
        capacity_known = bool(int(capacity.get("capacity_known", 0) or 0))
        pieces_per_day = max(0, int(capacity.get("pieces_per_day", 0) or 0))
        horizon_days = max(1, int(capacity.get("horizon_days", 14) or 14))
        fulfillment_lead_days = max(0, int(capacity.get("fulfillment_lead_days", 0) or 0))
        emergency_cover_days = max(1, int(capacity.get("emergency_cover_days", 7) or 7))
        expedited_fbo_lead_days = max(0, int(capacity.get("expedited_fbo_lead_days", 3) or 3))
        fbs_lead_days = max(0, int(capacity.get("fbs_lead_days", 0) or 0))
        try:
            workdays = {int(x) for x in str(capacity.get("workdays", "0,1,2,3,4,5")).split(",") if str(x).strip() != ""}
        except ValueError:
            workdays = {0, 1, 2, 3, 4, 5}
        if not workdays:
            workdays = {0, 1, 2, 3, 4, 5}

        def next_workday(d: date) -> date:
            candidate = d
            while candidate.weekday() not in workdays:
                candidate += timedelta(days=1)
            return candidate

        first_schedule_date = next_workday(today_msk + timedelta(days=1))
        plan["Крайний срок производства"] = plan["Дата обнуления до поставки"].apply(
            lambda value: pd.Timestamp(value) - pd.Timedelta(days=fulfillment_lead_days)
            if pd.notna(value) else pd.NaT
        )
        plan["Срок производства уже пропущен"] = plan.apply(
            lambda r: bool(int(r.get("Рекомендовано, компл.", 0) or 0) > 0
                           and pd.notna(r.get("Крайний срок производства"))
                           and pd.Timestamp(r["Крайний срок производства"]).date() < first_schedule_date), axis=1
        )

        schedule_rows: list[dict] = []
        first_production_dates: dict[int, date] = {}
        completion_dates: dict[int, date] = {}
        scheduled_by_date: dict[date, int] = {}
        blocked_by_material: dict[int, int] = {}
        material_available_by_key: dict[str, float] = {}
        material_known_by_key: dict[str, bool] = {}
        if not material_plan.empty:
            for _, material_row in material_plan.iterrows():
                key = material_key(str(material_row.get("Материал / цвет", "")))
                known = bool(material_row.get("Остаток указан", False))
                material_known_by_key[key] = known
                if known:
                    material_available_by_key[key] = max(0.0, float(material_row.get("На складе, м", 0) or 0))

        if capacity_known and pieces_per_day > 0:
            calendar_cursor = [first_schedule_date]
            pending = {
                int(idx): int(row["Рекомендовано, компл."])
                for idx, row in plan[plan["Рекомендовано, компл."] > 0].iterrows()
            }

            def round_up_batch(value: float, batch: int) -> int:
                if value <= 0:
                    return 0
                return int(math.ceil(float(value) / max(1, int(batch))) * max(1, int(batch)))

            def put_into_calendar(row_index: int, kits: int, stage: str) -> int:
                if kits <= 0:
                    return 0
                row = plan.loc[row_index]
                remaining = int(kits)
                scheduled_total = 0
                pack_size = max(1, int(row["Штук в комплекте"]))
                batch_size = max(1, int(row["Мин. партия, компл."]))
                nm_id = int(row["Артикул WB"])
                material_rate = max(0.0, float(row.get("Материал на ед., м", 0) or 0))
                material_name = str(row.get("Материал / цвет", "") or "")
                m_key = material_key(material_name)
                material_known = bool(material_known_by_key.get(m_key, False))
                while remaining > 0:
                    current_date = next_workday(calendar_cursor[0])
                    calendar_cursor[0] = current_date
                    used = scheduled_by_date.get(current_date, 0)
                    available = max(0, pieces_per_day - used)
                    kits_fit = available // pack_size
                    if kits_fit <= 0:
                        calendar_cursor[0] = next_workday(current_date + timedelta(days=1))
                        continue
                    if remaining <= kits_fit:
                        kits_today = remaining
                    else:
                        kits_today = (kits_fit // batch_size) * batch_size
                        if kits_today <= 0:
                            calendar_cursor[0] = next_workday(current_date + timedelta(days=1))
                            continue
                    if material_known and material_rate > 0:
                        available_m = max(0.0, float(material_available_by_key.get(m_key, 0.0)))
                        max_kits_material = int(math.floor((available_m + 1e-9) / material_rate))
                        max_kits_material = (max_kits_material // batch_size) * batch_size
                        if max_kits_material <= 0:
                            blocked_by_material[nm_id] = max(blocked_by_material.get(nm_id, 0), remaining)
                            break
                        kits_today = min(kits_today, max_kits_material)
                        kits_today = (kits_today // batch_size) * batch_size
                        if kits_today <= 0:
                            blocked_by_material[nm_id] = max(blocked_by_material.get(nm_id, 0), remaining)
                            break
                    pieces_today = kits_today * pack_size
                    material_today = kits_today * material_rate
                    schedule_rows.append({
                        "Дата": current_date, "Этап": stage, "Приоритет": row["Приоритет"],
                        "Артикул WB": nm_id, "Артикул продавца": row["Артикул продавца"],
                        "Товар": row["Товар"], "Тип заготовки": row["Тип заготовки"],
                        "Материал / цвет": material_name, "Комплектов": kits_today,
                        "Штук": pieces_today, "Материал, м": material_today,
                    })
                    if material_known and material_rate > 0:
                        material_available_by_key[m_key] = max(0.0, material_available_by_key.get(m_key, 0.0) - material_today)
                    scheduled_by_date[current_date] = used + pieces_today
                    remaining -= kits_today
                    scheduled_total += kits_today
                    first_production_dates.setdefault(nm_id, current_date)
                    completion_dates[nm_id] = current_date
                    if scheduled_by_date[current_date] >= pieces_per_day:
                        calendar_cursor[0] = next_workday(current_date + timedelta(days=1))
                return scheduled_total

            # Emergency pass: distribute one minimum batch at a time among all
            # cards whose WB stock is below the selected bridge-stock threshold.
            emergency_targets: dict[int, int] = {}
            emergency_order = plan[plan["Рекомендовано, компл."] > 0].copy()
            emergency_order = emergency_order.sort_values(
                ["Крайний срок производства", "Запас WB, дней", "Расчётная прибыль"],
                ascending=[True, True, False], na_position="last"
            )
            for idx, row in emergency_order.iterrows():
                velocity = max(0.0, float(row.get("Продаж/день", 0) or 0))
                stock_wb = max(0.0, float(row.get("Остаток", 0) or 0))
                recommended = int(row.get("Рекомендовано, компл.", 0) or 0)
                batch = max(1, int(row.get("Мин. партия, компл.", 1) or 1))
                bridge_need = max(velocity * emergency_cover_days - stock_wb, 0.0)
                emergency_targets[int(idx)] = min(recommended, round_up_batch(bridge_need, batch))

            while any(value > 0 for value in emergency_targets.values()):
                progressed = False
                for idx, row in emergency_order.iterrows():
                    idx = int(idx)
                    remaining_emergency = int(emergency_targets.get(idx, 0))
                    if remaining_emergency <= 0:
                        continue
                    batch = max(1, int(row.get("Мин. партия, компл.", 1) or 1))
                    chunk = min(batch, remaining_emergency)
                    scheduled_chunk = put_into_calendar(idx, chunk, "Аварийный запас")
                    if scheduled_chunk > 0:
                        emergency_targets[idx] -= scheduled_chunk
                        pending[idx] = max(0, int(pending.get(idx, 0)) - scheduled_chunk)
                        progressed = True
                    else:
                        emergency_targets[idx] = 0
                if not progressed:
                    break

            # Main pass: after all urgent cards have received a bridge batch,
            # finish the target stock in priority order and group similar jobs.
            main_order = plan[plan["Рекомендовано, компл."] > 0].sort_values(
                ["_priority", "Крайний срок производства", "Тип заготовки", "Материал / цвет", "Расчётная прибыль"],
                ascending=[True, True, True, True, False], na_position="last"
            )
            for idx, _row in main_order.iterrows():
                idx = int(idx)
                remaining = int(pending.get(idx, 0))
                if remaining > 0:
                    put_into_calendar(idx, remaining, "Основной план")

        schedule = pd.DataFrame(schedule_rows)
        if not schedule.empty:
            group_cols = [
                "Дата", "Этап", "Приоритет", "Артикул WB", "Артикул продавца", "Товар",
                "Тип заготовки", "Материал / цвет"
            ]
            schedule = schedule.groupby(group_cols, as_index=False, dropna=False).agg({
                "Комплектов": "sum", "Штук": "sum", "Материал, м": "sum"
            }).sort_values(["Дата", "Этап", "Приоритет", "Артикул продавца"])

        scheduled_by_nm: dict[int, int] = {}
        if not schedule.empty:
            scheduled_by_nm = schedule.groupby("Артикул WB")["Комплектов"].sum().astype(int).to_dict()
        plan["Запланировано по наличию сырья, компл."] = plan["Артикул WB"].map(scheduled_by_nm).fillna(0).astype(int)
        plan["Заблокировано сырьём, компл."] = plan["Артикул WB"].map(blocked_by_material).fillna(0).astype(int)
        plan["Материал ограничивает план"] = plan["Заблокировано сырьём, компл."].gt(0)
        if not material_plan.empty:
            reserved_by_material: dict[str, float] = {}
            if not schedule.empty:
                reserved_by_material = schedule.groupby("Материал / цвет")["Материал, м"].sum().to_dict()
            material_plan["Зарезервировано календарём, м"] = material_plan["Материал / цвет"].map(reserved_by_material).fillna(0.0)
            material_plan["Остаток после календаря, м"] = material_plan["На складе, м"] - material_plan["Зарезервировано календарём, м"]
            material_plan.loc[~material_plan["Остаток указан"], "Остаток после календаря, м"] = float("nan")

        plan["Плановая дата первого выпуска"] = plan["Артикул WB"].map(first_production_dates)
        plan["Плановая дата завершения"] = plan["Артикул WB"].map(completion_dates)
        plan["Плановая дата первого пополнения WB"] = plan["Плановая дата первого выпуска"].apply(
            lambda value: pd.Timestamp(value) + pd.Timedelta(days=fulfillment_lead_days)
            if pd.notna(value) else pd.NaT
        )
        plan["Не успеваем до обнуления"] = plan.apply(
            lambda r: bool(pd.notna(r["Плановая дата первого пополнения WB"])
                           and pd.notna(r["Дата обнуления до поставки"])
                           and pd.Timestamp(r["Плановая дата первого пополнения WB"])
                           > pd.Timestamp(r["Дата обнуления до поставки"])), axis=1
        )

        # Shipment plan v3.2. It separates what must be produced from what must be
        # dispatched, shows the expected stockout gap and highlights cases where
        # production can be completed but the normal WB delivery lead time is too long.
        def _round_to_batch(value: float, batch: int) -> int:
            if value <= 0:
                return 0
            batch = max(1, int(batch))
            return int(math.ceil(float(value) / batch) * batch)

        emergency_by_nm: dict[int, int] = {}
        if not schedule.empty and "Артикул WB" in schedule.columns:
            emergency_by_nm = (
                schedule[schedule["Этап"] == "Аварийный запас"]
                .groupby("Артикул WB")["Комплектов"].sum().astype(int).to_dict()
            )
        plan["Аварийная партия по плану, компл."] = plan["Артикул WB"].map(emergency_by_nm).fillna(0).astype(int)

        normal_dispatch_arrival = pd.Timestamp(today_msk + timedelta(days=fulfillment_lead_days))
        plan["Дата обычной срочной поставки"] = normal_dispatch_arrival

        def _nearest_replenishment(row: pd.Series):
            values = []
            if bool(row.get("Поставка указана", False)) and float(row.get("В пути учтено", 0) or 0) > 0:
                inbound_date = row.get("Дата прибытия")
                if pd.notna(inbound_date):
                    values.append(pd.Timestamp(inbound_date).normalize())
            production_date = row.get("Плановая дата первого пополнения WB")
            if pd.notna(production_date):
                values.append(pd.Timestamp(production_date).normalize())
            return min(values) if values else pd.NaT

        plan["Ближайшее пополнение WB"] = plan.apply(_nearest_replenishment, axis=1)
        plan["Ожидаемый разрыв, дней"] = plan.apply(
            lambda r: max(
                0,
                int((pd.Timestamp(r["Ближайшее пополнение WB"]).normalize()
                     - pd.Timestamp(r["Дата обнуления до поставки"]).normalize()).days),
            )
            if pd.notna(r.get("Ближайшее пополнение WB")) and pd.notna(r.get("Дата обнуления до поставки"))
            else float("nan"),
            axis=1,
        )
        plan["Период отсутствия"] = plan.apply(
            lambda r: (
                f"{pd.Timestamp(r['Дата обнуления до поставки']):%d.%m.%Y}–"
                f"{pd.Timestamp(r['Ближайшее пополнение WB']):%d.%m.%Y}"
            )
            if pd.notna(r.get("Ожидаемый разрыв, дней")) and float(r.get("Ожидаемый разрыв, дней", 0) or 0) > 0
            else ("Нет разрыва" if pd.notna(r.get("Ближайшее пополнение WB")) else "Нет даты пополнения"),
            axis=1,
        )

        def _urgent_dispatch_qty(row: pd.Series) -> int:
            velocity = max(0.0, float(row.get("Продаж/день", 0) or 0))
            if velocity <= 0:
                return 0
            batch = max(1, int(row.get("Мин. партия, компл.", 1) or 1))
            stock_now = max(0.0, float(row.get("Остаток", 0) or 0))
            projected_stock = max(stock_now - velocity * fulfillment_lead_days, 0.0)
            if bool(row.get("Поставка указана", False)) and float(row.get("В пути учтено", 0) or 0) > 0:
                inbound_date = row.get("Дата прибытия")
                if pd.notna(inbound_date) and pd.Timestamp(inbound_date).normalize() <= normal_dispatch_arrival.normalize():
                    projected_stock += float(row.get("В пути учтено", 0) or 0)
            emergency_target = velocity * emergency_cover_days
            return _round_to_batch(max(emergency_target - projected_stock, 0.0), batch)

        plan["Минимум срочно отгрузить, компл."] = plan.apply(_urgent_dispatch_qty, axis=1)
        plan["Готово к отгрузке, компл."] = plan["Готово учтено"].where(plan["Локальный остаток известен"], float("nan"))
        plan["Не хватает готового резерва, компл."] = plan.apply(
            lambda r: max(
                int(r.get("Минимум срочно отгрузить, компл.", 0) or 0)
                - int(r.get("Готово учтено", 0) or 0),
                0,
            )
            if bool(r.get("Локальный остаток известен", False)) else float("nan"),
            axis=1,
        )

        def _shipment_risk(row: pd.Series) -> str:
            velocity = float(row.get("Продаж/день", 0) or 0)
            if velocity <= 0:
                return "Нет спроса"
            stockout = row.get("Дата обнуления до поставки")
            replenishment = row.get("Ближайшее пополнение WB")
            if pd.isna(stockout):
                return "Нет прогноза"
            if pd.notna(replenishment) and pd.Timestamp(replenishment) <= pd.Timestamp(stockout):
                return "Без разрыва"
            inbound_date = row.get("Дата прибытия")
            production_date = row.get("Плановая дата первого выпуска")
            production_arrival = row.get("Плановая дата первого пополнения WB")
            if bool(row.get("Поставка указана", False)) and float(row.get("В пути учтено", 0) or 0) > 0                     and pd.notna(inbound_date) and pd.Timestamp(inbound_date) > pd.Timestamp(stockout):
                if pd.isna(production_arrival) or pd.Timestamp(inbound_date) <= pd.Timestamp(production_arrival):
                    return "Поставка в пути опоздает"
            if pd.notna(production_date) and pd.notna(production_arrival):
                if pd.Timestamp(production_date) <= pd.Timestamp(stockout) < pd.Timestamp(production_arrival):
                    return "Производство успевает, логистика нет"
                if pd.Timestamp(production_date) > pd.Timestamp(stockout):
                    return "Производство и логистика не успевают"
                return "Ожидается разрыв"
            return "Нет запланированного пополнения"

        plan["Риск отгрузки"] = plan.apply(_shipment_risk, axis=1)

        def _production_recommendation(row: pd.Series) -> str:
            qty = int(row.get("Рекомендовано, компл.", 0) or 0)
            if str(row.get("Статус", "")) == "Убыточный":
                return "Не производить до исправления экономики"
            if qty <= 0:
                return "Новый выпуск не требуется"
            first_date = row.get("Плановая дата первого выпуска")
            emergency_qty = int(row.get("Аварийная партия по плану, компл.", 0) or 0)
            if pd.notna(first_date):
                if emergency_qty > 0:
                    return f"Сначала {emergency_qty} компл. {pd.Timestamp(first_date):%d.%m}; всего {qty} компл."
                return f"Произвести {qty} компл. с {pd.Timestamp(first_date):%d.%m}"
            return f"Произвести {qty} компл.; нет места в календаре"

        def _dispatch_recommendation(row: pd.Series) -> str:
            urgent = int(row.get("Минимум срочно отгрузить, компл.", 0) or 0)
            if float(row.get("Продаж/день", 0) or 0) <= 0:
                return "Срочная отгрузка не требуется"
            if str(row.get("Риск отгрузки", "")) == "Без разрыва":
                inbound = int(row.get("В пути учтено", 0) or 0)
                return f"Поставка успевает; контролировать приёмку {inbound} компл."
            if not bool(row.get("Локальный остаток известен", False)):
                return f"Указать готовый остаток; ориентир срочной отгрузки {urgent} компл."
            ready = int(row.get("Готово учтено", 0) or 0)
            if urgent <= 0:
                return "Контролировать дату ближайшего пополнения"
            if ready >= urgent:
                return f"Отгрузить {urgent} готовых комплектов сегодня"
            if ready > 0:
                return f"Отгрузить {ready} сейчас; срочно добрать ещё {urgent - ready} компл."
            return f"Готового резерва нет: срочно подготовить {urgent} компл.; рассмотреть FBS/ускоренную поставку"

        plan["Изготовить"] = plan.apply(_production_recommendation, axis=1)
        plan["Отгрузить"] = plan.apply(_dispatch_recommendation, axis=1)

        # Operational action plan v3.3. The bridge quantity only closes the
        # expected stockout period. The first-dispatch target additionally restores
        # the configured emergency cover, so the two numbers remain transparent.
        def _bridge_to_replenishment_qty(row: pd.Series) -> int:
            velocity = max(0.0, float(row.get("Продаж/день", 0) or 0))
            gap_days = row.get("Ожидаемый разрыв, дней")
            if velocity <= 0 or pd.isna(gap_days) or float(gap_days) <= 0:
                return 0
            batch = max(1, int(row.get("Мин. партия, компл.", 1) or 1))
            return _round_to_batch(velocity * float(gap_days), batch)

        plan["Закрыть разрыв, компл."] = plan.apply(_bridge_to_replenishment_qty, axis=1)
        plan["Цель первой отгрузки, компл."] = plan[[
            "Закрыть разрыв, компл.", "Минимум срочно отгрузить, компл."
        ]].max(axis=1).fillna(0).astype(int)
        plan["FBS / ускорение, компл."] = plan.apply(
            lambda r: int(r.get("Закрыть разрыв, компл.", 0) or 0)
            if str(r.get("Риск отгрузки", "")) in {
                "Производство и логистика не успевают",
                "Производство успевает, логистика нет",
                "Поставка в пути опоздает",
                "Ожидается разрыв",
                "Нет запланированного пополнения",
            }
            else 0,
            axis=1,
        )

        def _fbs_action(row: pd.Series) -> str:
            bridge = int(row.get("FBS / ускорение, компл.", 0) or 0)
            gap_value = row.get("Ожидаемый разрыв, дней", 0)
            gap = int(float(gap_value)) if pd.notna(gap_value) else 0
            if bridge <= 0:
                return "Не требуется"
            ready_known = bool(row.get("Локальный остаток известен", False))
            ready = int(row.get("Готово учтено", 0) or 0)
            if ready_known and ready >= bridge:
                return f"Ускоренно отгрузить {bridge} компл.; FBS оставить резервом на {gap} дн."
            if ready_known and ready > 0:
                return f"Отгрузить {ready} компл.; ещё {bridge - ready} компл. держать под FBS/ускорение"
            if ready_known:
                return f"После выпуска держать {bridge} компл. под FBS/ускоренную схему ({gap} дн.)"
            return f"Подтвердить готовый остаток; ориентир FBS/ускорения {bridge} компл. на {gap} дн."

        plan["FBS / ускоренная схема"] = plan.apply(_fbs_action, axis=1)

        plan["Ограничить рекламу до"] = plan["Ближайшее пополнение WB"].copy()

        def _ad_action(row: pd.Series) -> str:
            spend = max(0.0, float(row.get("Реклама", 0) or 0))
            ad_share = max(0.0, float(row.get("Доля рекламы, %", 0) or 0))
            gap = max(0.0, float(row.get("Ожидаемый разрыв, дней", 0) or 0))
            stock_days = row.get("Запас WB, дней")
            status = str(row.get("Статус", ""))
            until = row.get("Ограничить рекламу до")
            until_text = f" до {pd.Timestamp(until):%d.%m.%Y}" if pd.notna(until) else " до восстановления остатка"
            if spend < 1:
                return "Рекламы нет"
            if status == "Убыточный":
                return "Остановить рекламу до исправления экономики"
            if gap > 0:
                if status in {"Лидер", "Прибыльный"}:
                    if ad_share >= 20:
                        return f"Снизить ставки на 40–50%{until_text}"
                    return f"Снизить ставки на 20–30%{until_text}"
                return f"Приостановить рекламу{until_text}"
            if pd.notna(stock_days) and float(stock_days) < float(emergency_cover_days):
                return f"Не масштабировать; ограничить дневной бюджет{until_text}"
            return "Не менять"

        plan["Рекламное действие"] = plan.apply(_ad_action, axis=1)
        plan["Вернуть обычный режим"] = plan.apply(
            lambda r: f"Вернуть обычные ставки с {pd.Timestamp(r['Ограничить рекламу до']):%d.%m.%Y}"
            if str(r.get("Рекламное действие", "")) not in {"Не менять", "Рекламы нет", "Остановить рекламу до исправления экономики"}
            and pd.notna(r.get("Ограничить рекламу до"))
            else ("После исправления экономики" if str(r.get("Рекламное действие", "")) == "Остановить рекламу до исправления экономики" else "—"),
            axis=1,
        )

        def _operational_priority(row: pd.Series) -> str:
            bridge = int(row.get("Закрыть разрыв, компл.", 0) or 0)
            ready = int(row.get("Готово учтено", 0) or 0)
            emergency_qty = int(row.get("Аварийная партия по плану, компл.", 0) or 0)
            if bridge > 0 and ready > 0:
                return "1 — Отгрузить готовое"
            if emergency_qty > 0:
                return "1 — Аварийное производство"
            if bridge > 0:
                return "1 — FBS / ускорение"
            if int(row.get("Рекомендовано, компл.", 0) or 0) > 0:
                return "2 — Плановое производство"
            return "3 — Контроль"

        plan["Оперативный приоритет"] = plan.apply(_operational_priority, axis=1)

        def _today_action(row: pd.Series) -> str:
            actions: list[str] = []
            target = int(row.get("Цель первой отгрузки, компл.", 0) or 0)
            ready_known = bool(row.get("Локальный остаток известен", False))
            ready = int(row.get("Готово учтено", 0) or 0)
            emergency_qty = int(row.get("Аварийная партия по плану, компл.", 0) or 0)
            bridge = int(row.get("Закрыть разрыв, компл.", 0) or 0)
            if target > 0:
                if ready_known and ready > 0:
                    actions.append(f"отгрузить сейчас {min(ready, target)} компл.")
                elif not ready_known:
                    actions.append("подтвердить готовый остаток")
            if emergency_qty > 0:
                actions.append(f"аварийно изготовить {emergency_qty} компл.")
            if bridge > 0:
                actions.append(f"закрыть разрыв минимум {bridge} компл.")
            ad_action = str(row.get("Рекламное действие", ""))
            if ad_action not in {"", "Не менять", "Рекламы нет"}:
                actions.append(ad_action.casefold())
            return "; ".join(actions) if actions else "Только контроль"

        plan["Действия сейчас"] = plan.apply(_today_action, axis=1)

        total_kits = int(plan["Рекомендовано, компл."].sum())
        total_pieces = int(plan["Штук к производству"].sum())
        total_material = float(plan["Нужно материала, м"].sum())
        scheduled_kits_total = int(schedule["Комплектов"].sum()) if not schedule.empty else 0
        scheduled_pieces_total = int(schedule["Штук"].sum()) if not schedule.empty else 0
        scheduled_material_total = float(schedule["Материал, м"].sum()) if not schedule.empty else 0.0
        material_blocked_total = int(plan.get("Заблокировано сырьём, компл.", pd.Series(dtype=float)).sum())
        ready_total = int(plan["Готово учтено"].sum())
        inbound_total = int(plan["В пути учтено"].sum())
        urgent_count = int(plan["Приоритет"].isin(["Срочно", "Высокий"]).sum())
        stop_count = int((plan["Приоритет"] == "Стоп").sum())
        risk_inbound_count = int(plan["Риск до поставки"].sum())
        late_count = int(plan["Не успеваем до обнуления"].sum())
        overdue_deadline_count = int(plan["Срок производства уже пропущен"].sum())
        norm_coverage = float((plan["Материал на ед., м"] > 0).mean() * 100) if len(plan) else 100.0
        material_groups = int(len(material_plan))
        known_material_groups = int(material_plan["Остаток указан"].sum()) if not material_plan.empty else 0
        known_stock_total = float(material_plan.loc[material_plan["Остаток указан"], "На складе, м"].sum()) if known_material_groups else 0.0
        known_shortage = float(material_plan.loc[material_plan["Остаток указан"], "Не хватает, м"].sum()) if known_material_groups else 0.0
        rolls_to_buy_total = int(material_plan.loc[material_plan["Остаток указан"], "Рулонов докупить"].sum()) if known_material_groups else 0
        unknown_need = float(material_plan.loc[~material_plan["Остаток указан"], "Потребность, м"].sum()) if material_groups else 0.0

        workdays_in_horizon = sum(
            1 for offset in range(1, horizon_days + 1)
            if (today_msk + timedelta(days=offset)).weekday() in workdays
        )
        horizon_capacity = workdays_in_horizon * pieces_per_day if capacity_known else 0
        overload_pieces = max(scheduled_pieces_total - horizon_capacity, 0) if capacity_known else 0
        production_days = int(math.ceil(scheduled_pieces_total / pieces_per_day)) if capacity_known and pieces_per_day > 0 else 0

        prod_kpis = st.columns(6)
        with prod_kpis[0]: kpi_card("Комплектов в календаре", num(scheduled_kits_total), f"Потребность {num(total_kits)} · блок сырья {num(material_blocked_total)}")
        with prod_kpis[1]: kpi_card("Штук в календаре", num(scheduled_pieces_total), f"Готово {num(ready_total)} · в пути {num(inbound_total)} компл.")
        with prod_kpis[2]: kpi_card("Сырьё зарезервировано", f"{scheduled_material_total:,.1f} м".replace(",", " "), f"Валовая потребность {total_material:.1f} м")
        with prod_kpis[3]: kpi_card("Производственная мощность", f"{num(pieces_per_day)} шт./день" if capacity_known else "—", f"Мост {emergency_cover_days} дн. · до WB {fulfillment_lead_days} дн." if capacity_known else "Заполните в настройках")
        with prod_kpis[4]: kpi_card("Срочные позиции", num(urgent_count), f"Риск до поставки: {risk_inbound_count}")
        with prod_kpis[5]: kpi_card("Риски плана", num(late_count + stop_count), f"Не успеваем пополнить {late_count} · стоп {stop_count}")

        if capacity_known and overload_pieces > 0:
            st.error(
                f"План перегружен на {num(overload_pieces)} штук в горизонте {horizon_days} дней. "
                "Нужно увеличить мощность, продлить горизонт или сократить целевой запас."
            )
        if fulfillment_lead_days <= 0:
            st.info(
                "Срок от выпуска до появления товара на WB пока равен 0 дней. "
                "Риски рассчитаны только по производству; укажите фактический срок доставки и приёмки в настройках."
            )
        if overdue_deadline_count > 0:
            st.warning(
                f"По {overdue_deadline_count} позициям крайний срок производства уже наступил или прошёл. "
                "Даже аварийная партия может не успеть до обнуления — нужен готовый резерв, ускоренная поставка или временный FBS."
            )
        elif late_count > 0:
            st.warning(f"По {late_count} позициям первое плановое пополнение WB позже возможного обнуления запаса.")
        if risk_inbound_count > 0:
            st.warning(f"По {risk_inbound_count} поставкам текущий запас может закончиться раньше даты прибытия.")
        unknown_local = int((~plan["Локальный остаток известен"]).sum())
        unknown_inbound = int((~plan["Поставка указана"]).sum())
        if unknown_local or unknown_inbound:
            st.info(
                f"Данные вне WB заполнены не полностью: готовая продукция не указана для {unknown_local} товаров, "
                f"поставки — для {unknown_inbound}. Неотмеченные значения в расчёт не включаются."
            )

        st.markdown("### Потребность по материалам и цветам")
        st.caption(
            "Остаток сырья единый по цвету: один рулон может использоваться для любых типов заготовок. "
            "Разбивка показывает, какая часть потребности приходится на каждый тип."
        )
        material_columns = [
            "Материал / цвет", *breakdown_columns, "Потребность, м",
            "Остаток указан", "Полных рулонов", "Открытый остаток, м", "На складе, м",
            "Не хватает, м", "Рулонов использовать", "Рулонов докупить",
            "Зарезервировано календарём, м", "Остаток после календаря, м",
            "Остаток после плана, м", "Статус сырья"
        ]
        if material_plan.empty:
            st.info("Материал пока не требуется по текущему плану.")
        else:
            st.dataframe(
                material_plan[material_columns], hide_index=True, use_container_width=True,
                height=min(420, 92 + 36 * max(len(material_plan), 1)),
                column_config={
                    **{column: st.column_config.NumberColumn(format="%.1f") for column in breakdown_columns},
                    "Потребность, м": st.column_config.NumberColumn(format="%.1f"),
                    "Остаток указан": st.column_config.CheckboxColumn("Остаток указан"),
                    "Открытый остаток, м": st.column_config.NumberColumn(format="%.1f"),
                    "На складе, м": st.column_config.NumberColumn(format="%.1f"),
                    "Не хватает, м": st.column_config.NumberColumn(format="%.1f"),
                    "Остаток после плана, м": st.column_config.NumberColumn(format="%.1f"),
                },
            )
            st.download_button(
                "Скачать потребность по сырью CSV",
                data=material_plan[material_columns].to_csv(index=False).encode("utf-8-sig"),
                file_name=f"material_plan_{start:%Y%m%d}_{end:%Y%m%d}.csv",
                mime="text/csv",
            )
        if known_material_groups < material_groups:
            st.info(
                f"Остатки сырья указаны для {known_material_groups} из {material_groups} цветов. "
                f"Потребность без известного остатка: {unknown_need:.1f} м."
            )
        elif known_shortage > 0:
            st.warning(f"Известный дефицит сырья: {known_shortage:.1f} м, ориентировочно докупить {rolls_to_buy_total} рул.")
        elif known_material_groups:
            st.success(f"Сырья по указанным цветам достаточно. На складе учтено {known_stock_total:.1f} м.")

        blocked_material_total = int(plan.get("Заблокировано сырьём, компл.", pd.Series(dtype=float)).sum())
        blocked_material_skus = int(plan.get("Материал ограничивает план", pd.Series(dtype=bool)).sum())
        if blocked_material_total > 0:
            st.error(
                f"Сырья недостаточно для полного плана: заблокировано {blocked_material_total} комплектов "
                f"по {blocked_material_skus} артикулам. Календарь ниже содержит только физически выполнимый объём."
            )

        st.markdown("### Календарный план")
        st.caption(
            f"Сначала программа распределяет минимальные аварийные партии, чтобы дать каждому срочному товару "
            f"около {emergency_cover_days} дней запаса, затем закрывает основной план. "
            f"Появление на WB считается через {fulfillment_lead_days} дней после выпуска."
        )
        if not capacity_known or pieces_per_day <= 0:
            st.info("Укажите производительность в «Настройки» → «Производственная мощность», и программа разложит план по рабочим дням.")
        elif schedule.empty:
            st.success("По текущим остаткам производство в календарь не требуется.")
        else:
            daily_summary = schedule.groupby("Дата", as_index=False).agg({
                "Комплектов": "sum", "Штук": "sum", "Материал, м": "sum"
            })
            daily_summary["Загрузка, %"] = daily_summary["Штук"] / pieces_per_day * 100
            calendar_kpis = st.columns(3)
            with calendar_kpis[0]: kpi_card("Рабочих дней в плане", num(daily_summary["Дата"].nunique()), f"Горизонт настройки: {horizon_days} дней")
            with calendar_kpis[1]: kpi_card("Средняя загрузка", pct(float(daily_summary["Загрузка, %"].mean())), f"Мощность {num(pieces_per_day)} шт./день")
            _last_batch_wb_date = (schedule["Дата"].max() + timedelta(days=fulfillment_lead_days)).strftime("%d.%m.%Y")
            with calendar_kpis[2]: kpi_card("Завершение плана", schedule["Дата"].max().strftime("%d.%m.%Y"), f"Появление последней партии на WB: {_last_batch_wb_date}")
            st.dataframe(
                schedule, hide_index=True, use_container_width=True, height=min(460, 92 + 36 * len(schedule)),
                column_config={
                    "Дата": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "Комплектов": st.column_config.NumberColumn(format="%d"),
                    "Штук": st.column_config.NumberColumn(format="%d"),
                    "Материал, м": st.column_config.NumberColumn(format="%.1f"),
                },
            )
            st.download_button(
                "Скачать календарный план CSV",
                data=schedule.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"production_calendar_{start:%Y%m%d}_{end:%Y%m%d}.csv",
                mime="text/csv",
            )

        st.markdown("### План отгрузок")
        st.caption(
            "План отделяет производство от доставки: показывает прогноз обнуления, ближайшее пополнение, "
            "ожидаемый период отсутствия и минимальную срочную отгрузку для восстановления аварийного запаса. "
            "Количество срочной отгрузки рассчитано на момент обычного появления товара на WB."
        )
        shipment_plan = plan.copy()
        shipment_plan = shipment_plan[
            (shipment_plan["Продаж/день"] > 0)
            & (
                (shipment_plan["Рекомендовано, компл."] > 0)
                | (shipment_plan["В пути учтено"] > 0)
                | (shipment_plan["Ожидаемый разрыв, дней"].fillna(0) > 0)
            )
        ].copy()
        shipment_risk_order = {
            "Производство и логистика не успевают": 0,
            "Производство успевает, логистика нет": 1,
            "Поставка в пути опоздает": 2,
            "Ожидается разрыв": 3,
            "Нет запланированного пополнения": 4,
            "Без разрыва": 5,
            "Нет прогноза": 6,
            "Нет спроса": 7,
        }
        shipment_plan["_shipment_risk"] = shipment_plan["Риск отгрузки"].map(shipment_risk_order).fillna(9)
        shipment_plan = shipment_plan.sort_values(
            ["_shipment_risk", "Ожидаемый разрыв, дней", "Запас WB, дней", "Расчётная прибыль"],
            ascending=[True, False, True, False], na_position="last"
        )

        gap_positions = int((shipment_plan["Ожидаемый разрыв, дней"].fillna(0) > 0).sum())
        max_gap_days = int(shipment_plan["Ожидаемый разрыв, дней"].fillna(0).max()) if not shipment_plan.empty else 0
        urgent_dispatch_total = int(shipment_plan["Минимум срочно отгрузить, компл."].sum()) if not shipment_plan.empty else 0
        known_shortfall_mask = shipment_plan["Локальный остаток известен"] & shipment_plan["Не хватает готового резерва, компл."].notna()
        ready_shortfall_total = int(shipment_plan.loc[known_shortfall_mask, "Не хватает готового резерва, компл."].sum()) if known_shortfall_mask.any() else 0
        logistics_late_count = int((shipment_plan["Риск отгрузки"] == "Производство успевает, логистика нет").sum())
        shipment_unknown_ready = int((~shipment_plan["Локальный остаток известен"]).sum())

        shipping_kpis = st.columns(4)
        with shipping_kpis[0]: kpi_card("Риск отсутствия", num(gap_positions), f"Максимальный разрыв {max_gap_days} дн.")
        with shipping_kpis[1]: kpi_card("Срочная отгрузка", num(urgent_dispatch_total), "Минимум комплектов")
        with shipping_kpis[2]: kpi_card("Не хватает готового", num(ready_shortfall_total), f"Остаток известен не везде: {shipment_unknown_ready}")
        with shipping_kpis[3]: kpi_card("Логистика не успевает", num(logistics_late_count), f"Обычный срок {fulfillment_lead_days} дн.")

        if logistics_late_count > 0:
            st.warning(
                f"По {logistics_late_count} позициям производство можно выполнить до обнуления, но обычная доставка "
                f"за {fulfillment_lead_days} дней не успеет. Нужна ускоренная отгрузка, готовый резерв или временный FBS."
            )
        if shipment_unknown_ready > 0:
            st.info(
                f"Готовый остаток не подтверждён по {shipment_unknown_ready} позициям. "
                "Ориентир срочной отгрузки рассчитан, но фактическую возможность отгрузить нужно подтвердить в настройках."
            )

        shipment_columns = [
            "Риск отгрузки", "Приоритет", "Артикул продавца", "Товар", "Остаток",
            "Продаж/день", "Запас WB, дней", "Дата обнуления до поставки", "Ближайшее пополнение WB",
            "Ожидаемый разрыв, дней", "Период отсутствия", "Готово к отгрузке, компл.",
            "В пути учтено", "Дата прибытия", "Закрыть разрыв, компл.",
            "Минимум срочно отгрузить, компл.", "Цель первой отгрузки, компл.",
            "Не хватает готового резерва, компл.", "Аварийная партия по плану, компл.",
            "Плановая дата первого выпуска", "Плановая дата первого пополнения WB",
            "FBS / ускоренная схема", "Рекламное действие", "Изготовить", "Отгрузить"
        ]
        if shipment_plan.empty:
            st.success("Срочных отгрузок и ожидаемых разрывов по текущему плану нет.")
        else:
            st.dataframe(
                shipment_plan[shipment_columns], hide_index=True, use_container_width=True,
                height=min(520, 92 + 36 * max(len(shipment_plan), 1)),
                column_config={
                    "Остаток": st.column_config.NumberColumn("Остаток WB", format="%.0f"),
                    "Продаж/день": st.column_config.NumberColumn(format="%.2f"),
                    "Запас WB, дней": st.column_config.NumberColumn(format="%.1f"),
                    "Дата обнуления до поставки": st.column_config.DateColumn("Прогноз обнуления", format="DD.MM.YYYY"),
                    "Ближайшее пополнение WB": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "Ожидаемый разрыв, дней": st.column_config.NumberColumn(format="%.0f"),
                    "Готово к отгрузке, компл.": st.column_config.NumberColumn(format="%.0f"),
                    "В пути учтено": st.column_config.NumberColumn("В пути, компл.", format="%.0f"),
                    "Дата прибытия": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "Закрыть разрыв, компл.": st.column_config.NumberColumn(format="%.0f"),
                    "Минимум срочно отгрузить, компл.": st.column_config.NumberColumn(format="%.0f"),
                    "Цель первой отгрузки, компл.": st.column_config.NumberColumn(format="%.0f"),
                    "Не хватает готового резерва, компл.": st.column_config.NumberColumn(format="%.0f"),
                    "Аварийная партия по плану, компл.": st.column_config.NumberColumn(format="%.0f"),
                    "Плановая дата первого выпуска": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "Плановая дата первого пополнения WB": st.column_config.DateColumn(format="DD.MM.YYYY"),
                },
            )
            st.download_button(
                "Скачать план отгрузок CSV",
                data=shipment_plan[shipment_columns].to_csv(index=False).encode("utf-8-sig"),
                file_name=f"shipment_plan_{start:%Y%m%d}_{end:%Y%m%d}.csv",
                mime="text/csv",
            )

        st.markdown("### Оперативный план действий")
        st.caption(
            "Сводка на ближайшую рабочую смену: что изготовить, что поставить в первую отгрузку, "
            "какой объём нужен только для закрытия ожидаемого разрыва, где рассмотреть FBS/ускорение "
            "и по каким карточкам временно ограничить рекламу."
        )

        next_shift_date = schedule["Дата"].min() if not schedule.empty else pd.NaT
        next_shift = schedule[schedule["Дата"] == next_shift_date].copy() if pd.notna(next_shift_date) else pd.DataFrame()
        next_shift_kits = int(next_shift["Комплектов"].sum()) if not next_shift.empty else 0
        next_shift_pieces = int(next_shift["Штук"].sum()) if not next_shift.empty else 0
        gap_action_plan = shipment_plan[shipment_plan["Ожидаемый разрыв, дней"].fillna(0) > 0].copy()
        first_dispatch_total = int(gap_action_plan["Цель первой отгрузки, компл."].sum()) if not gap_action_plan.empty else 0
        bridge_total = int(gap_action_plan["Закрыть разрыв, компл."].sum()) if not gap_action_plan.empty else 0
        fbs_count = int((gap_action_plan["FBS / ускорение, компл."] > 0).sum()) if not gap_action_plan.empty else 0
        ad_change_mask = ~plan["Рекламное действие"].isin(["Не менять", "Рекламы нет"])
        ad_change_count = int(ad_change_mask.sum())

        ready_now_total = int(gap_action_plan.apply(
            lambda r: min(
                int(r.get("Готово учтено", 0) or 0),
                int(r.get("Цель первой отгрузки, компл.", 0) or 0),
            ) if bool(r.get("Локальный остаток известен", False)) else 0,
            axis=1,
        ).sum()) if not gap_action_plan.empty else 0

        action_kpis = st.columns(5)
        with action_kpis[0]: kpi_card("Готово отгрузить сейчас", num(ready_now_total), "Подтверждённый готовый остаток")
        with action_kpis[1]:
            shift_label = pd.Timestamp(next_shift_date).strftime("%d.%m.%Y") if pd.notna(next_shift_date) else "—"
            kpi_card("Ближайшая смена", num(next_shift_kits), f"{shift_label} · {num(next_shift_pieces)} штук")
        with action_kpis[2]: kpi_card("Только закрыть разрыв", num(bridge_total), "Минимальный мост до пополнения")
        with action_kpis[3]: kpi_card("Аварийное пополнение", num(first_dispatch_total), f"По {len(gap_action_plan)} позициям")
        with action_kpis[4]: kpi_card("FBS / ускорение", num(fbs_count), f"Макс. разрыв {max_gap_days} дн.")

        action_tabs = st.tabs(["Изготовить", "Отгрузить / FBS", "Реклама"])
        with action_tabs[0]:
            if next_shift.empty:
                st.info("На ближайшую рабочую смену производственные задания не сформированы.")
            else:
                next_shift_view = next_shift[[
                    "Дата", "Этап", "Приоритет", "Артикул продавца", "Товар", "Тип заготовки",
                    "Материал / цвет", "Комплектов", "Штук", "Материал, м"
                ]].copy()
                st.dataframe(
                    next_shift_view, hide_index=True, use_container_width=True,
                    height=min(430, 92 + 36 * len(next_shift_view)),
                    column_config={
                        "Дата": st.column_config.DateColumn(format="DD.MM.YYYY"),
                        "Комплектов": st.column_config.NumberColumn(format="%.0f"),
                        "Штук": st.column_config.NumberColumn(format="%.0f"),
                        "Материал, м": st.column_config.NumberColumn(format="%.1f"),
                    },
                )
        with action_tabs[1]:
            if gap_action_plan.empty:
                st.success("Ожидаемых разрывов и срочных схем отгрузки нет.")
            else:
                dispatch_view = gap_action_plan[[
                    "Оперативный приоритет", "Артикул продавца", "Товар", "Остаток",
                    "Запас WB, дней", "Дата обнуления до поставки", "Ближайшее пополнение WB",
                    "Ожидаемый разрыв, дней", "Закрыть разрыв, компл.",
                    "Цель первой отгрузки, компл.", "Готово к отгрузке, компл.",
                    "FBS / ускоренная схема", "Действия сейчас"
                ]].copy()
                st.dataframe(
                    dispatch_view, hide_index=True, use_container_width=True,
                    height=min(500, 92 + 36 * len(dispatch_view)),
                    column_config={
                        "Остаток": st.column_config.NumberColumn("Остаток WB", format="%.0f"),
                        "Запас WB, дней": st.column_config.NumberColumn(format="%.1f"),
                        "Дата обнуления до поставки": st.column_config.DateColumn("Прогноз обнуления", format="DD.MM.YYYY"),
                        "Ближайшее пополнение WB": st.column_config.DateColumn(format="DD.MM.YYYY"),
                        "Ожидаемый разрыв, дней": st.column_config.NumberColumn(format="%.0f"),
                        "Закрыть разрыв, компл.": st.column_config.NumberColumn(format="%.0f"),
                        "Цель первой отгрузки, компл.": st.column_config.NumberColumn(format="%.0f"),
                        "Готово к отгрузке, компл.": st.column_config.NumberColumn(format="%.0f"),
                    },
                )
        with action_tabs[2]:
            ad_actions = plan[ad_change_mask].copy().sort_values(
                ["Ожидаемый разрыв, дней", "Запас WB, дней", "Доля рекламы, %"],
                ascending=[False, True, False], na_position="last"
            )
            if ad_actions.empty:
                st.success("Карточек, по которым нужно временно менять рекламу, нет.")
            else:
                ad_view = ad_actions[[
                    "Артикул продавца", "Товар", "Статус", "Запас WB, дней",
                    "Ожидаемый разрыв, дней", "Реклама", "Доля рекламы, %",
                    "Расчётная маржа, %", "Ограничить рекламу до", "Рекламное действие", "Вернуть обычный режим"
                ]].copy()
                st.dataframe(
                    ad_view, hide_index=True, use_container_width=True,
                    height=min(470, 92 + 36 * len(ad_view)),
                    column_config={
                        "Запас WB, дней": st.column_config.NumberColumn(format="%.1f"),
                        "Ожидаемый разрыв, дней": st.column_config.NumberColumn(format="%.0f"),
                        "Реклама": st.column_config.NumberColumn(format="%.0f ₽"),
                        "Доля рекламы, %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Расчётная маржа, %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Ограничить рекламу до": st.column_config.DateColumn(format="DD.MM.YYYY"),
                    },
                )

        st.markdown("### Исполнение плана")
        st.caption(
            "Фиксируйте фактический выпуск и отгрузки. В версии 3.6 операции проводятся отдельными безопасными "
            "кнопками: закрытие смены добавляет упакованные комплекты в готовый остаток, проведение отгрузки "
            "списывает их и создаёт товар в пути, а приёмка WB закрывает поставку. Каждое движение записывается "
            "в журнал и защищено от повторного проведения."
        )

        movement_flash = st.session_state.pop("movement_flash", None)
        if movement_flash:
            level, text = movement_flash
            if level == "success":
                st.success(text)
            elif level == "warning":
                st.warning(text)
            else:
                st.error(text)

        execution_saved = read_table("execution_tasks")
        if execution_saved.empty:
            execution_saved = pd.DataFrame(columns=[
                "task_key", "task_type", "task_date", "stage", "nm_id", "supplier_article",
                "product_name", "planned_units", "actual_units", "status", "route",
                "dispatch_date", "expected_arrival_date", "note", "updated_at"
            ])

        def _merge_execution(base: pd.DataFrame) -> pd.DataFrame:
            if base.empty:
                return base
            mutable = [
                "actual_units", "status", "route", "dispatch_date",
                "expected_arrival_date", "note"
            ]
            saved = execution_saved.copy()
            if saved.empty:
                return base
            saved = saved[["task_key"] + [c for c in mutable if c in saved.columns]].copy()
            saved = saved.rename(columns={c: f"{c}_saved" for c in mutable if c in saved.columns})
            result = base.merge(saved, on="task_key", how="left")
            for col in mutable:
                saved_col = f"{col}_saved"
                if saved_col not in result.columns:
                    continue
                if col in {"actual_units"}:
                    result[col] = pd.to_numeric(result[saved_col], errors="coerce").fillna(result[col])
                elif col in {"dispatch_date", "expected_arrival_date"}:
                    saved_dates = pd.to_datetime(result[saved_col], errors="coerce")
                    base_dates = pd.to_datetime(result[col], errors="coerce")
                    result[col] = saved_dates.where(saved_dates.notna(), base_dates)
                else:
                    saved_text = result[saved_col].fillna("").astype(str)
                    result[col] = saved_text.where(saved_text.str.len() > 0, result[col])
                result = result.drop(columns=[saved_col], errors="ignore")
            return result

        wip_runtime = wip_module_status()
        production_statuses = [
            "Не начато", "В производстве", "Изготовлено", "Упаковано", "Передано на отгрузку"
        ]
        dispatch_statuses = [
            "Не начато", "Готово к отгрузке", "Отгружено", "Принято WB", "Закрыто"
        ]
        dispatch_routes = ["Не выбрано", "FBS", "Ускоренная FBO", "Стандартная FBO"]
        route_lead_days = {
            "FBS": fbs_lead_days,
            "Ускоренная FBO": expedited_fbo_lead_days,
            "Стандартная FBO": fulfillment_lead_days,
        }

        production_exec = pd.DataFrame()
        if not schedule.empty:
            production_exec = schedule.copy()
            pack_by_nm = plan.set_index("Артикул WB")["Штук в комплекте"].to_dict()
            production_exec["Штук в комплекте"] = production_exec["Артикул WB"].map(pack_by_nm).fillna(4).astype(int)
            production_exec["task_date"] = pd.to_datetime(production_exec["Дата"], errors="coerce")
            production_exec["task_key"] = production_exec.apply(
                lambda r: f"production|{pd.Timestamp(r['task_date']):%Y-%m-%d}|{int(r['Артикул WB'])}|{r['Этап']}", axis=1
            )
            production_exec["task_type"] = "production"
            production_exec["stage"] = production_exec["Этап"]
            production_exec["nm_id"] = production_exec["Артикул WB"].astype(int)
            production_exec["supplier_article"] = production_exec["Артикул продавца"].astype(str)
            production_exec["product_name"] = production_exec["Товар"].astype(str)
            production_exec["planned_units"] = production_exec["Комплектов"].astype(int)
            production_exec["actual_units"] = 0
            production_exec["status"] = "Не начато"
            production_exec["route"] = ""
            production_exec["dispatch_date"] = pd.NaT
            production_exec["expected_arrival_date"] = pd.NaT
            production_exec["note"] = ""
            production_exec = _merge_execution(production_exec)
            production_exec["actual_units"] = pd.to_numeric(production_exec["actual_units"], errors="coerce").fillna(0).clip(lower=0).astype(int)

        dispatch_exec = pd.DataFrame()
        if not gap_action_plan.empty:
            dispatch_exec = gap_action_plan.copy()
            dispatch_exec["task_date"] = pd.Timestamp(today_msk)
            dispatch_exec["task_key"] = dispatch_exec.apply(
                lambda r: (
                    f"dispatch|{int(r['Артикул WB'])}|"
                    f"{pd.Timestamp(r['Дата обнуления до поставки']):%Y-%m-%d}"
                    if pd.notna(r.get("Дата обнуления до поставки"))
                    else f"dispatch|{int(r['Артикул WB'])}|{today_msk:%Y-%m-%d}"
                ), axis=1
            )
            dispatch_exec["task_type"] = "dispatch"
            dispatch_exec["stage"] = "Срочная отгрузка"
            dispatch_exec["nm_id"] = dispatch_exec["Артикул WB"].astype(int)
            dispatch_exec["supplier_article"] = dispatch_exec["Артикул продавца"].astype(str)
            dispatch_exec["product_name"] = dispatch_exec["Товар"].astype(str)
            dispatch_exec["planned_units"] = dispatch_exec["Цель первой отгрузки, компл."].fillna(0).astype(int)
            dispatch_exec["actual_units"] = 0
            dispatch_exec["status"] = "Не начато"
            dispatch_exec["route"] = "Не выбрано"
            dispatch_exec["dispatch_date"] = pd.NaT
            dispatch_exec["expected_arrival_date"] = pd.NaT
            dispatch_exec["note"] = ""
            dispatch_exec = _merge_execution(dispatch_exec)
            dispatch_exec["actual_units"] = pd.to_numeric(dispatch_exec["actual_units"], errors="coerce").fillna(0).clip(lower=0).astype(int)
            dispatch_exec["route"] = dispatch_exec["route"].replace("", "Не выбрано")

        # v3.7.2: keep the generated calendar and dispatch plan synchronized with
        # the execution table automatically. Previously the “Сегодня” page could
        # show 0/0 until the user manually pressed “Сохранить исполнение смены”.
        execution_columns = [
            "task_key", "task_type", "task_date", "stage", "nm_id", "supplier_article",
            "product_name", "planned_units", "actual_units", "status", "route",
            "dispatch_date", "expected_arrival_date", "note"
        ]
        production_to_sync = (
            production_exec[execution_columns].copy()
            if not production_exec.empty
            else pd.DataFrame(columns=execution_columns)
        )
        dispatch_to_sync = (
            dispatch_exec[execution_columns].copy()
            if not dispatch_exec.empty
            else pd.DataFrame(columns=execution_columns)
        )
        sync_generated_execution_tasks(production_to_sync, "production", today_msk)
        sync_generated_execution_tasks(dispatch_to_sync, "dispatch", today_msk)

        def _prepare_production_rows(editor_df: pd.DataFrame) -> pd.DataFrame:
            prepared = editor_df.copy()
            # v3.6: factual quantities are never inferred from the plan.
            # This prevents a completed status with zero fact from silently booking the full plan.
            prepared["actual_units"] = pd.to_numeric(
                prepared["actual_units"], errors="coerce"
            ).fillna(0).clip(lower=0).astype(int)
            prepared["route"] = ""
            prepared["dispatch_date"] = pd.NaT
            prepared["expected_arrival_date"] = pd.NaT
            return prepared

        def _prepare_dispatch_rows(editor_df: pd.DataFrame) -> pd.DataFrame:
            prepared = editor_df.copy()
            for idx, row in prepared.iterrows():
                status = str(row.get("status", ""))
                route = str(row.get("route", "Не выбрано"))
                dispatch_date = pd.to_datetime(row.get("dispatch_date"), errors="coerce")
                expected = pd.to_datetime(row.get("expected_arrival_date"), errors="coerce")
                if status in {"Отгружено", "Принято WB", "Закрыто"} and pd.isna(dispatch_date):
                    dispatch_date = pd.Timestamp(today_msk)
                    prepared.at[idx, "dispatch_date"] = dispatch_date
                if pd.notna(dispatch_date) and pd.isna(expected) and route in route_lead_days:
                    prepared.at[idx, "expected_arrival_date"] = dispatch_date + pd.Timedelta(days=route_lead_days[route])
            prepared["actual_units"] = pd.to_numeric(
                prepared["actual_units"], errors="coerce"
            ).fillna(0).clip(lower=0).astype(int)
            return prepared

        def _movement_message(action: str, result: dict[str, object]) -> tuple[str, str]:
            posted = int(result.get("posted", 0) or 0)
            skipped = int(result.get("skipped", 0) or 0)
            units = int(result.get("units", 0) or 0)
            meters = float(result.get("meters", 0) or 0)
            goods_cost = float(result.get("cost_rub", 0) or 0)
            wip_units = int(result.get("wip_units", 0) or 0)
            errors = list(result.get("errors", []) or [])
            base = f"{action}: проведено {posted} операций, {units} комплектов"
            if wip_units > 0:
                base += f", использовано {wip_units} заготовок из НЗП"
            if meters > 0:
                base += f", списано {meters:.1f} м сырья"
            if goods_cost > 0:
                base += f", стоимость партий {money(goods_cost)}"
            if skipped:
                base += f", пропущено повторных/неподходящих — {skipped}"
            if errors:
                return "error", base + ". Ошибки: " + " | ".join(str(x) for x in errors)
            return "success", base + ". Остатки и план пересчитаны."

        execution_tabs = st.tabs(["Сменное задание", "Отгрузки", "Печатная форма", "Журнал движений", "Себестоимость партий", "НЗП / заготовки"])
        with execution_tabs[0]:
            if production_exec.empty:
                st.info("Производственные задания пока не сформированы.")
            else:
                production_dates = sorted(pd.to_datetime(production_exec["task_date"]).dt.date.unique())
                selected_execution_date = st.selectbox(
                    "Рабочая смена", production_dates,
                    format_func=lambda d: pd.Timestamp(d).strftime("%d.%m.%Y"),
                    key="production_execution_date",
                )
                prod_day = production_exec[
                    pd.to_datetime(production_exec["task_date"]).dt.date == selected_execution_date
                ].copy()
                prod_day["Факт штук"] = prod_day["actual_units"] * prod_day["Штук в комплекте"]
                prod_summary = prod_day.groupby(
                    ["Тип заготовки", "Материал / цвет"], as_index=False, dropna=False
                ).agg({
                    "planned_units": "sum", "Штук": "sum", "Материал, м": "sum",
                    "actual_units": "sum", "Факт штук": "sum"
                }).rename(columns={
                    "planned_units": "План, компл.", "Штук": "План, шт.",
                    "actual_units": "Факт, компл.", "Факт штук": "Факт, шт."
                })
                st.dataframe(
                    prod_summary, hide_index=True, use_container_width=True,
                    column_config={
                        "План, компл.": st.column_config.NumberColumn(format="%.0f"),
                        "План, шт.": st.column_config.NumberColumn(format="%.0f"),
                        "Материал, м": st.column_config.NumberColumn(format="%.1f"),
                        "Факт, компл.": st.column_config.NumberColumn(format="%.0f"),
                        "Факт, шт.": st.column_config.NumberColumn(format="%.0f"),
                    },
                )
                prod_editor = st.data_editor(
                    prod_day[[
                        "task_key", "task_type", "task_date", "stage", "nm_id", "supplier_article",
                        "product_name", "Тип заготовки", "Материал / цвет", "Штук в комплекте",
                        "planned_units", "actual_units", "status", "note"
                    ]],
                    hide_index=True, use_container_width=True, num_rows="fixed",
                    disabled=[
                        "task_key", "task_type", "task_date", "stage", "nm_id", "supplier_article",
                        "product_name", "Тип заготовки", "Материал / цвет", "Штук в комплекте", "planned_units"
                    ],
                    column_config={
                        "task_key": None, "task_type": None, "nm_id": None,
                        "task_date": st.column_config.DateColumn("Дата", format="DD.MM.YYYY"),
                        "stage": "Этап", "supplier_article": "Артикул продавца",
                        "product_name": "Товар", "planned_units": st.column_config.NumberColumn("План, компл.", format="%.0f"),
                        "actual_units": st.column_config.NumberColumn("Факт, компл.", min_value=0, step=1, format="%d"),
                        "status": st.column_config.SelectboxColumn("Статус", options=production_statuses, required=True),
                        "note": st.column_config.TextColumn("Примечание"),
                    },
                    key=f"production_execution_editor_{selected_execution_date}",
                )
                close_statuses = ["Упаковано", "Передано на отгрузку"] if bool(wip_runtime.get("enabled")) else ["Изготовлено", "Упаковано", "Передано на отгрузку"]
                if bool(wip_runtime.get("enabled")):
                    st.info(
                        "Контур НЗП включён: закрытие смены списывает отдельные заготовки по FIFO и оприходует только упакованные комплекты. "
                        "Статус «Изготовлено» означает незавершённую продукцию и сам по себе не закрывает смену."
                    )
                close_ready_mask = (
                    prod_editor["status"].isin(close_statuses)
                    & (pd.to_numeric(prod_editor["actual_units"], errors="coerce").fillna(0) > 0)
                )
                close_ready_units = int(pd.to_numeric(
                    prod_editor.loc[close_ready_mask, "actual_units"], errors="coerce"
                ).fillna(0).sum())
                if close_ready_units <= 0:
                    st.info("Для закрытия смены укажите фактическое количество больше нуля и итоговый статус хотя бы по одной строке.")
                confirm_close_shift = st.checkbox(
                    f"Подтверждаю оприходование {close_ready_units} комплектов",
                    key=f"confirm_close_shift_{selected_execution_date}",
                    disabled=close_ready_units <= 0,
                )
                prod_actions = st.columns(2)
                with prod_actions[0]:
                    if st.button(
                        "Сохранить исполнение смены",
                        key=f"save_production_execution_{selected_execution_date}",
                        use_container_width=True,
                    ):
                        to_save = _prepare_production_rows(prod_editor)
                        save_execution_tasks(to_save[[
                            "task_key", "task_type", "task_date", "stage", "nm_id", "supplier_article",
                            "product_name", "planned_units", "actual_units", "status", "route",
                            "dispatch_date", "expected_arrival_date", "note"
                        ]])
                        st.success("Фактический выпуск и статусы смены сохранены.")
                        st.cache_data.clear()
                with prod_actions[1]:
                    if st.button(
                        "Закрыть смену и оприходовать",
                        key=f"close_production_shift_{selected_execution_date}",
                        type="primary",
                        use_container_width=True,
                        help=(
                            "Добавляет фактические упакованные комплекты в готовый остаток. Если контур НЗП включён, списывает заготовки по типу и цвету; "
                            "иначе использует прежний прямой расход сырья. При недостаточном остатке операция блокируется. Повторное нажатие не дублирует движения."
                        ),
                        disabled=not (confirm_close_shift and close_ready_units > 0),
                    ):
                        to_save = _prepare_production_rows(prod_editor)
                        save_execution_tasks(to_save[[
                            "task_key", "task_type", "task_date", "stage", "nm_id", "supplier_article",
                            "product_name", "planned_units", "actual_units", "status", "route",
                            "dispatch_date", "expected_arrival_date", "note"
                        ]])
                        result = close_production_shift(selected_execution_date)
                        st.session_state["movement_flash"] = _movement_message("Закрытие смены", result)
                        st.cache_data.clear()
                        st.rerun()

        with execution_tabs[1]:
            if dispatch_exec.empty:
                st.success("Срочных задач на отгрузку нет.")
            else:
                st.caption(
                    f"Сроки маршрутов: FBS — {fbs_lead_days} дн., ускоренная FBO — {expedited_fbo_lead_days} дн., "
                    f"стандартная FBO — {fulfillment_lead_days} дн. Их можно изменить в настройках мощности."
                )
                dispatch_editor = st.data_editor(
                    dispatch_exec[[
                        "task_key", "task_type", "task_date", "stage", "nm_id", "supplier_article", "product_name",
                        "Риск отгрузки", "Ожидаемый разрыв, дней", "Закрыть разрыв, компл.",
                        "planned_units", "actual_units", "route", "status", "dispatch_date",
                        "expected_arrival_date", "note"
                    ]],
                    hide_index=True, use_container_width=True, num_rows="fixed",
                    disabled=[
                        "task_key", "task_type", "task_date", "stage", "nm_id", "supplier_article", "product_name",
                        "Риск отгрузки", "Ожидаемый разрыв, дней", "Закрыть разрыв, компл.", "planned_units"
                    ],
                    column_config={
                        "task_key": None, "task_type": None, "nm_id": None, "task_date": None, "stage": None,
                        "supplier_article": "Артикул продавца", "product_name": "Товар",
                        "Ожидаемый разрыв, дней": st.column_config.NumberColumn(format="%.0f"),
                        "Закрыть разрыв, компл.": st.column_config.NumberColumn(format="%.0f"),
                        "planned_units": st.column_config.NumberColumn("Цель, компл.", format="%.0f"),
                        "actual_units": st.column_config.NumberColumn("Факт отгрузки, компл.", min_value=0, step=1, format="%d"),
                        "route": st.column_config.SelectboxColumn("Маршрут", options=dispatch_routes, required=True),
                        "status": st.column_config.SelectboxColumn("Статус", options=dispatch_statuses, required=True),
                        "dispatch_date": st.column_config.DateColumn("Дата отгрузки", format="DD.MM.YYYY"),
                        "expected_arrival_date": st.column_config.DateColumn("Ожидаемое появление", format="DD.MM.YYYY"),
                        "note": st.column_config.TextColumn("Примечание"),
                    },
                    key="dispatch_execution_editor",
                )
                dispatch_prepared_preview = _prepare_dispatch_rows(dispatch_editor)
                postable_mask = (
                    dispatch_prepared_preview["status"].eq("Отгружено")
                    & (dispatch_prepared_preview["actual_units"] > 0)
                    & ~dispatch_prepared_preview["route"].isin(["", "Не выбрано"])
                )
                receivable_mask = (
                    dispatch_prepared_preview["status"].isin(["Принято WB", "Закрыто"])
                    & (dispatch_prepared_preview["actual_units"] > 0)
                    & ~dispatch_prepared_preview["route"].isin(["", "Не выбрано", "FBS"])
                )
                postable_units = int(dispatch_prepared_preview.loc[postable_mask, "actual_units"].sum())
                receivable_units = int(dispatch_prepared_preview.loc[receivable_mask, "actual_units"].sum())
                confirm_dispatch = st.checkbox(
                    f"Подтверждаю списание и проведение {postable_units} комплектов",
                    key="confirm_dispatch_posting", disabled=postable_units <= 0,
                )
                confirm_receipt = st.checkbox(
                    f"Подтверждаю приёмку WB по {receivable_units} комплектам",
                    key="confirm_wb_receipt", disabled=receivable_units <= 0,
                )
                dispatch_actions = st.columns(3)
                with dispatch_actions[0]:
                    if st.button("Сохранить статусы", key="save_dispatch_execution", use_container_width=True):
                        to_save = _prepare_dispatch_rows(dispatch_editor)
                        save_execution_tasks(to_save[[
                            "task_key", "task_type", "task_date", "stage", "nm_id", "supplier_article",
                            "product_name", "planned_units", "actual_units", "status", "route",
                            "dispatch_date", "expected_arrival_date", "note"
                        ]])
                        st.success("Статусы, маршрут и даты сохранены. Пустые даты рассчитаны по маршруту.")
                        st.cache_data.clear()
                with dispatch_actions[1]:
                    if st.button(
                        "Провести отгрузки",
                        key="post_dispatch_execution",
                        type="primary",
                        use_container_width=True,
                        help="Списывает фактически отгруженные комплекты из готового остатка. Для FBO одновременно создаёт товар в пути. Повторное нажатие не дублирует операцию.",
                        disabled=not (confirm_dispatch and postable_units > 0),
                    ):
                        to_save = _prepare_dispatch_rows(dispatch_editor)
                        save_execution_tasks(to_save[[
                            "task_key", "task_type", "task_date", "stage", "nm_id", "supplier_article",
                            "product_name", "planned_units", "actual_units", "status", "route",
                            "dispatch_date", "expected_arrival_date", "note"
                        ]])
                        keys_to_post = to_save.loc[to_save["status"].eq("Отгружено"), "task_key"].astype(str).tolist()
                        result = post_dispatches(keys_to_post)
                        st.session_state["movement_flash"] = _movement_message("Отгрузка", result)
                        st.cache_data.clear()
                        st.rerun()
                with dispatch_actions[2]:
                    if st.button(
                        "Зафиксировать приёмку WB",
                        key="post_wb_receipt_execution",
                        use_container_width=True,
                        help="Закрывает товар в пути по строкам со статусом «Принято WB» или «Закрыто». Остаток на самом WB затем подтверждается следующей API-синхронизацией.",
                        disabled=not (confirm_receipt and receivable_units > 0),
                    ):
                        to_save = _prepare_dispatch_rows(dispatch_editor)
                        save_execution_tasks(to_save[[
                            "task_key", "task_type", "task_date", "stage", "nm_id", "supplier_article",
                            "product_name", "planned_units", "actual_units", "status", "route",
                            "dispatch_date", "expected_arrival_date", "note"
                        ]])
                        keys_to_receive = to_save.loc[to_save["status"].isin(["Принято WB", "Закрыто"]), "task_key"].astype(str).tolist()
                        result = post_wb_receipts(keys_to_receive)
                        st.session_state["movement_flash"] = _movement_message("Приёмка WB", result)
                        st.cache_data.clear()
                        st.rerun()

        with execution_tabs[2]:
            if next_shift.empty:
                st.info("Нет сменного задания для печати.")
            else:
                print_date = pd.Timestamp(next_shift_date).strftime("%d.%m.%Y")
                grouped = next_shift.groupby(["Тип заготовки", "Материал / цвет"], as_index=False).agg({
                    "Комплектов": "sum", "Штук": "sum", "Материал, м": "sum"
                })
                summary_rows = "".join(
                    f"<tr><td>{escape(str(r['Тип заготовки']))}</td><td>{escape(str(r['Материал / цвет']))}</td>"
                    f"<td>{int(r['Комплектов'])}</td><td>{int(r['Штук'])}</td><td>{float(r['Материал, м']):.1f}</td></tr>"
                    for _, r in grouped.iterrows()
                )
                detail_rows = "".join(
                    f"<tr><td>□</td><td>{escape(str(r['Артикул продавца']))}</td><td>{escape(str(r['Товар']))}</td>"
                    f"<td>{escape(str(r['Тип заготовки']))}</td><td>{escape(str(r['Материал / цвет']))}</td>"
                    f"<td>{int(r['Комплектов'])}</td><td>{int(r['Штук'])}</td><td>{float(r['Материал, м']):.1f}</td>"
                    f"<td></td><td></td></tr>" for _, r in next_shift.iterrows()
                )
                shift_html = f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'>
                <title>Сменное задание {print_date}</title><style>
                body{{font-family:Arial,sans-serif;margin:24px;color:#111}} h1{{margin:0 0 6px}} .meta{{margin-bottom:18px;color:#444}}
                table{{border-collapse:collapse;width:100%;margin:12px 0 24px;font-size:12px}} th,td{{border:1px solid #777;padding:6px;vertical-align:top}}
                th{{background:#eee}} .sign{{display:flex;gap:50px;margin-top:30px}} .line{{width:280px;border-bottom:1px solid #111;height:28px}}
                @media print{{button{{display:none}} body{{margin:8mm}}}}
                </style></head><body><button onclick='window.print()'>Печать</button>
                <h1>Сменное задание</h1><div class='meta'>Дата: <b>{print_date}</b> · План: <b>{next_shift_kits} комплектов / {next_shift_pieces} штук</b></div>
                <h2>Сводка по материалам</h2><table><thead><tr><th>Тип заготовки</th><th>Материал / цвет</th><th>Комплектов</th><th>Штук</th><th>Материал, м</th></tr></thead><tbody>{summary_rows}</tbody></table>
                <h2>Задания</h2><table><thead><tr><th>Готово</th><th>Артикул</th><th>Товар</th><th>Заготовка</th><th>Цвет</th><th>План, компл.</th><th>План, шт.</th><th>Материал, м</th><th>Факт, компл.</th><th>Примечание</th></tr></thead><tbody>{detail_rows}</tbody></table>
                <div class='sign'><div>Мастер смены<div class='line'></div></div><div>Принял готовую продукцию<div class='line'></div></div></div>
                </body></html>"""
                st.download_button(
                    "Скачать печатное сменное задание HTML",
                    data=shift_html.encode("utf-8"),
                    file_name=f"shift_task_{pd.Timestamp(next_shift_date):%Y%m%d}.html",
                    mime="text/html",
                    use_container_width=True,
                )
                st.caption("Откройте скачанный HTML в браузере и нажмите «Печать». Форму можно распечатать или сохранить в PDF.")

        with execution_tabs[3]:
            movements = read_inventory_movements(500)
            pipeline_now = read_table("product_pipeline")
            total_ready_now = int(pd.to_numeric(pipeline_now.get("ready_units", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not pipeline_now.empty else 0
            total_inbound_now = int(pd.to_numeric(pipeline_now.get("inbound_units", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not pipeline_now.empty else 0
            active_count = 0
            if not movements.empty:
                active_count = int((movements["reversed_at"].isna() & movements["movement_type"].ne("reversal")).sum())
            active_material_spent = 0.0
            if not movements.empty and "material_delta" in movements.columns:
                active_mask = movements["reversed_at"].isna() & movements["movement_type"].ne("reversal")
                active_material_spent = -float(pd.to_numeric(movements.loc[active_mask, "material_delta"], errors="coerce").fillna(0).sum())
            movement_kpis = st.columns(4)
            with movement_kpis[0]: kpi_card("Готово на производстве", num(total_ready_now), "Комплектов после проведённых движений")
            with movement_kpis[1]: kpi_card("В пути на WB", num(total_inbound_now), "FBO-поставки, ещё не принятые WB")
            with movement_kpis[2]: kpi_card("Сырья списано", f"{active_material_spent:.1f} м", "По активным закрытым сменам")
            with movement_kpis[3]: kpi_card("Активные движения", num(active_count), "Производство, отгрузки и приёмки")

            st.caption(
                "Журнал является контрольной книгой локального учёта. Отмена выполняет обратное движение и не удаляет историю. "
                "Если операция уже повлекла следующее движение, сначала отмените более позднюю операцию."
            )
            if movements.empty:
                st.info("Проведённых движений пока нет. Закройте смену или проведите первую отгрузку.")
            else:
                movement_labels = {
                    "production_receipt": "Оприходование производства (прямое сырьё)",
                    "wip_material_issue": "Выдача сырья в НЗП",
                    "wip_blank_receipt": "Оприходование заготовок",
                    "production_receipt_wip": "Комплектация из НЗП",
                    "dispatch": "Отгрузка",
                    "wb_receipt": "Приёмка WB",
                    "reversal": "Отмена операции",
                }
                movement_view = movements.copy()
                movement_view["Операция"] = movement_view["movement_type"].map(movement_labels).fillna(movement_view["movement_type"])
                movement_view["Состояние"] = movement_view.apply(
                    lambda r: "Отменена" if pd.notna(r.get("reversed_at")) else (
                        "В пути" if r.get("movement_type") == "dispatch" and r.get("status") == "open" else
                        "Закрыта" if r.get("movement_type") == "dispatch" and r.get("status") == "closed" else
                        "Проведена"
                    ), axis=1
                )
                movement_view["Изменение готового"] = pd.to_numeric(movement_view["ready_delta"], errors="coerce").fillna(0).astype(int)
                movement_view["Изменение в пути"] = pd.to_numeric(movement_view["inbound_delta"], errors="coerce").fillna(0).astype(int)
                if "material_delta" not in movement_view.columns:
                    movement_view["material_delta"] = 0.0
                if "material_name" not in movement_view.columns:
                    movement_view["material_name"] = ""
                movement_view["Изменение сырья, м"] = pd.to_numeric(movement_view["material_delta"], errors="coerce").fillna(0.0)
                movement_view["Дата"] = pd.to_datetime(movement_view["movement_date"], errors="coerce")
                movement_view["Ожидаемое поступление"] = pd.to_datetime(movement_view["expected_arrival_date"], errors="coerce")
                st.dataframe(
                    movement_view[[
                        "id", "Дата", "Операция", "Состояние", "supplier_article", "product_name",
                        "quantity", "Изменение готового", "Изменение в пути", "material_name",
                        "Изменение сырья, м", "material_cost_rub", "unit_cost_rub",
                        "route", "Ожидаемое поступление", "note", "created_at"
                    ]],
                    hide_index=True, use_container_width=True, height=420,
                    column_config={
                        "id": st.column_config.NumberColumn("№", format="%d"),
                        "Дата": st.column_config.DateColumn(format="DD.MM.YYYY"),
                        "supplier_article": "Артикул продавца", "product_name": "Товар",
                        "quantity": st.column_config.NumberColumn("Количество", format="%d"),
                        "Изменение готового": st.column_config.NumberColumn(format="%d"),
                        "Изменение в пути": st.column_config.NumberColumn(format="%d"),
                        "material_name": "Материал / цвет",
                        "Изменение сырья, м": st.column_config.NumberColumn(format="%.3f"),
                        "material_cost_rub": st.column_config.NumberColumn("Стоимость сырья FIFO, ₽", format="%.2f"),
                        "unit_cost_rub": st.column_config.NumberColumn("Факт. себестоимость комплекта, ₽", format="%.2f"),
                        "route": "Маршрут", "Ожидаемое поступление": st.column_config.DateColumn(format="DD.MM.YYYY"),
                        "note": "Примечание", "created_at": "Записано",
                    },
                )

                cancellable = movements[
                    movements["reversed_at"].isna() & movements["movement_type"].ne("reversal")
                ].copy()
                if cancellable.empty:
                    st.info("Активных операций для отмены нет.")
                else:
                    cancel_options = cancellable["id"].astype(int).tolist()
                    label_by_id = {
                        int(r["id"]): (
                            f"№{int(r['id'])} · {movement_labels.get(str(r['movement_type']), str(r['movement_type']))} · "
                            f"{str(r.get('supplier_article', '') or r.get('material_name', '') or '')} · количество {int(r.get('quantity', 0) or 0)}"
                        ) for _, r in cancellable.iterrows()
                    }
                    selected_movement_id = st.selectbox(
                        "Отменить проведённую операцию",
                        cancel_options,
                        format_func=lambda value: label_by_id.get(int(value), str(value)),
                        key="movement_to_undo",
                    )
                    if st.button(
                        "Отменить выбранное движение",
                        key="undo_inventory_movement",
                        help="Создаёт обратную запись и восстанавливает готовый остаток/товар в пути. История не удаляется.",
                    ):
                        undo_result = undo_inventory_movement(int(selected_movement_id))
                        st.session_state["movement_flash"] = (
                            "success" if undo_result.get("ok") else "error",
                            str(undo_result.get("message", "")),
                        )
                        st.cache_data.clear()
                        st.rerun()

        with execution_tabs[4]:
            batches = read_production_cost_batches(500)
            active_batches = batches[batches.get("status", "").astype(str).eq("active")].copy() if not batches.empty else pd.DataFrame()
            if active_batches.empty:
                st.info("Фактических производственных партий пока нет. Они появятся после закрытия смены и списания сырья по FIFO.")
            else:
                active_batches["produced_units"] = pd.to_numeric(active_batches["produced_units"], errors="coerce").fillna(0)
                active_batches["total_cost_rub"] = pd.to_numeric(active_batches["total_cost_rub"], errors="coerce").fillna(0.0)
                active_batches["material_cost_rub"] = pd.to_numeric(active_batches["material_cost_rub"], errors="coerce").fillna(0.0)
                total_units = int(active_batches["produced_units"].sum())
                total_cost = float(active_batches["total_cost_rub"].sum())
                weighted_unit = total_cost / total_units if total_units else 0.0
                cols = st.columns(4)
                with cols[0]: kpi_card("Партий", num(len(active_batches)), "Проведённые смены")
                with cols[1]: kpi_card("Произведено", num(total_units), "Комплектов")
                with cols[2]: kpi_card("Сырьё FIFO", money(float(active_batches["material_cost_rub"].sum())), "Фактически списанная стоимость")
                with cols[3]: kpi_card("Средняя себестоимость", money(weighted_unit), "На произведённый комплект")
                st.caption(
                    "Стоимость каждой партии формируется из фактически списанных FIFO-слоёв сырья, упаковки, работы и прочих расходов. "
                    "Текущая фиксированная себестоимость финансового отчёта не перезаписывается автоматически."
                )
                batch_view = active_batches.copy()
                batch_view["batch_date"] = pd.to_datetime(batch_view["batch_date"], errors="coerce")
                st.dataframe(
                    batch_view[[
                        "id", "batch_date", "supplier_article", "product_name", "material_name",
                        "produced_units", "material_meters", "material_cost_rub", "packaging_cost_rub",
                        "labor_cost_rub", "other_cost_rub", "total_cost_rub", "unit_cost_rub", "note"
                    ]],
                    hide_index=True, use_container_width=True, height=420,
                    column_config={
                        "id": st.column_config.NumberColumn("Партия", format="%d"),
                        "batch_date": st.column_config.DateColumn("Дата", format="DD.MM.YYYY"),
                        "supplier_article": "Артикул продавца", "product_name": "Товар",
                        "material_name": "Материал / цвет",
                        "produced_units": st.column_config.NumberColumn("Комплектов", format="%d"),
                        "material_meters": st.column_config.NumberColumn("Материал, м", format="%.3f"),
                        "material_cost_rub": st.column_config.NumberColumn("Сырьё FIFO, ₽", format="%.2f"),
                        "packaging_cost_rub": st.column_config.NumberColumn("Упаковка, ₽", format="%.2f"),
                        "labor_cost_rub": st.column_config.NumberColumn("Работа, ₽", format="%.2f"),
                        "other_cost_rub": st.column_config.NumberColumn("Прочее, ₽", format="%.2f"),
                        "total_cost_rub": st.column_config.NumberColumn("Всего, ₽", format="%.2f"),
                        "unit_cost_rub": st.column_config.NumberColumn("На комплект, ₽", format="%.2f"),
                        "note": "Состав FIFO",
                    },
                )

        with execution_tabs[5]:
            st.markdown("#### НЗП / заготовки")
            st.caption(
                "Двухэтапный производственный контур: сырьё сначала переводится в незавершённое производство и превращается "
                "в отдельные заготовки, затем заготовки комплектуются и упаковываются в продаваемые наборы. Стоимость сырья "
                "переносится между этапами по FIFO без повторного списания."
            )

            wip_status = wip_module_status()
            wip_summary = read_wip_blank_summary()
            wip_batches = read_wip_blank_batches(active_only=True)
            wip_total_units = int(pd.to_numeric(wip_summary.get("remaining_units", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not wip_summary.empty else 0
            wip_total_value = float(pd.to_numeric(wip_summary.get("remaining_cost_rub", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not wip_summary.empty else 0.0
            # Breakdown by whatever blank types the tenant actually uses (any number
            # of freely-named types), not a fixed two-way split.
            if not wip_summary.empty:
                wip_type_breakdown = wip_summary.assign(
                    blank_type=wip_summary.get("blank_type", pd.Series(dtype=str)).astype(str).str.strip()
                ).groupby("blank_type", as_index=False)["remaining_units"].sum()
                wip_type_breakdown = wip_type_breakdown[wip_type_breakdown["blank_type"].ne("")]
            else:
                wip_type_breakdown = pd.DataFrame(columns=["blank_type", "remaining_units"])
            wip_type_count = int(len(wip_type_breakdown))
            wip_top_type = ""
            wip_top_units = 0
            if not wip_type_breakdown.empty:
                top_row = wip_type_breakdown.sort_values("remaining_units", ascending=False).iloc[0]
                wip_top_type = str(top_row["blank_type"])
                wip_top_units = int(top_row["remaining_units"])
            wip_cols = st.columns(5)
            with wip_cols[0]: kpi_card("Контур НЗП", "Включён" if wip_status.get("enabled") else "Не использовался", "После первой выдачи сырья становится обязательным")
            with wip_cols[1]: kpi_card("Заготовок в НЗП", num(wip_total_units), "Годные отдельные изделия")
            with wip_cols[2]: kpi_card(
                "Типов заготовок в НЗП", num(wip_type_count),
                f"Больше всего: {wip_top_type} — {num(wip_top_units)} шт." if wip_top_type else "Нет остатков",
            )
            with wip_cols[3]: kpi_card("Стоимость НЗП", money(wip_total_value), "Остаточная стоимость FIFO")
            with wip_cols[4]: kpi_card("Открытых партий", num(int(wip_status.get("open_batches", 0) or 0)), f"Выдано {float(wip_status.get('open_meters', 0) or 0):.1f} м, выпуск не посчитан")

            if wip_status.get("enabled"):
                st.info(
                    "Контур НЗП активен. Теперь кнопка «Закрыть смену и оприходовать» принимает только строки со статусом "
                    "«Упаковано» или «Передано на отгрузку» и списывает заготовки, а не сырьё напрямую."
                )

            wip_tabs = st.tabs(["Выпуск заготовок", "Остатки НЗП", "Комплектация", "Журнал НЗП"])
            with wip_tabs[0]:
                st.markdown("##### Выдать сырьё и выпустить заготовки")
                st.caption(
                    "Если количество годных заготовок ещё не посчитано, выберите режим «только выдать сырьё». "
                    "Партия останется открытой, а количество можно внести позднее без повторного списания материала."
                )
                material_inventory_wip = read_table("material_inventory_color")
                if material_inventory_wip.empty:
                    st.warning("Сначала заполните остатки сырья по цветам в настройках.")
                else:
                    material_inventory_wip = material_inventory_wip.copy()
                    material_inventory_wip["balance_known"] = pd.to_numeric(material_inventory_wip.get("balance_known", 0), errors="coerce").fillna(0).astype(int)
                    material_inventory_wip = material_inventory_wip[material_inventory_wip["balance_known"].eq(1)].copy()
                    material_inventory_wip["material_name"] = material_inventory_wip.get("material_name", "").fillna("").astype(str)
                    material_options = material_inventory_wip["material_name"].dropna().astype(str).str.strip()
                    material_options = sorted(x for x in material_options.unique().tolist() if x)
                    if not material_options:
                        st.warning("Нет подтверждённых остатков сырья по цветам.")
                    else:
                        issue_mode = st.radio(
                            "Способ проведения",
                            ["Списать сырьё и сразу оприходовать заготовки", "Только выдать сырьё в НЗП — количество внесу позже"],
                            horizontal=True,
                            key="wip_issue_mode",
                        )
                        issue_cols = st.columns(3)
                        with issue_cols[0]:
                            wip_batch_date = st.date_input("Дата производства", value=today_msk, key="wip_batch_date")
                            wip_material_name = st.selectbox("Материал / цвет", material_options, key="wip_material_name")
                        with issue_cols[1]:
                            # Offer whatever blank types the tenant has already configured
                            # in Settings (any number, freely named) instead of a fixed list;
                            # fall back to free text if nothing is configured yet.
                            configured_blank_types = sorted({
                                str(t).strip()
                                for t in read_table("production_settings").get("blank_type", pd.Series(dtype=str)).astype(str)
                                if str(t).strip() and str(t).strip() != "Не задано"
                            })
                            if configured_blank_types:
                                wip_blank_type = st.selectbox("Тип заготовки", configured_blank_types, key="wip_blank_type")
                            else:
                                wip_blank_type = st.text_input("Тип заготовки", key="wip_blank_type_text").strip()
                            selected_material_row = material_inventory_wip[material_inventory_wip["material_name"].astype(str).eq(str(wip_material_name))].head(1)
                            wip_roll_length = float(selected_material_row.iloc[0].get("roll_length", 25.5) or 25.5) if not selected_material_row.empty else 25.5
                            wip_full_rolls = st.number_input("Полных рулонов израсходовано", min_value=0, max_value=10000, value=0, step=1, key="wip_full_rolls")
                        with issue_cols[2]:
                            wip_partial_meters = st.number_input(
                                "Дополнительно из открытого рулона, м", min_value=0.0, max_value=float(max(1000.0, wip_roll_length * 10)),
                                value=0.0, step=0.25, format="%.3f", key="wip_partial_meters",
                            )
                            wip_total_meters = round(float(wip_full_rolls) * wip_roll_length + float(wip_partial_meters), 3)
                            st.metric("Итого к списанию", f"{wip_total_meters:.3f} м", f"Длина рулона {wip_roll_length:g} м")

                        cfg_wip = read_table("production_settings")
                        theoretical_units = 0
                        per_blank_rate = 0.0
                        if not cfg_wip.empty:
                            cfg_wip = cfg_wip.copy()
                            cfg_wip["enabled"] = pd.to_numeric(cfg_wip.get("enabled", 0), errors="coerce").fillna(0).astype(int)
                            cfg_wip["material_per_unit"] = pd.to_numeric(cfg_wip.get("material_per_unit", 0), errors="coerce").fillna(0.0)
                            cfg_wip["pack_size"] = pd.to_numeric(cfg_wip.get("pack_size", 1), errors="coerce").fillna(1).clip(lower=1)
                            matching_cfg = cfg_wip[
                                cfg_wip["enabled"].eq(1)
                                & cfg_wip.get("material_name", "").fillna("").astype(str).str.strip().eq(str(wip_material_name).strip())
                                & cfg_wip.get("blank_type", "").fillna("").astype(str).str.strip().eq(str(wip_blank_type).strip())
                            ].copy()
                            if not matching_cfg.empty:
                                rates = matching_cfg["material_per_unit"] / matching_cfg["pack_size"]
                                rates = rates[rates.gt(0)]
                                if not rates.empty:
                                    per_blank_rate = float(rates.median())
                                    theoretical_units = int(wip_total_meters // per_blank_rate) if per_blank_rate > 0 else 0
                        if per_blank_rate > 0 and wip_total_meters > 0:
                            st.caption(
                                f"Ориентир по сохранённой норме: {per_blank_rate:.5f} м на одну заготовку, теоретически около {theoretical_units} шт. "
                                "В учёт вносите фактическое количество после брака."
                            )

                        immediate = issue_mode.startswith("Списать сырьё")
                        output_cols = st.columns(3)
                        with output_cols[0]:
                            wip_produced_units = st.number_input(
                                "Годных заготовок, шт.", min_value=0, max_value=1000000,
                                value=int(theoretical_units) if immediate and theoretical_units > 0 else 0,
                                step=1, disabled=not immediate, key="wip_produced_units",
                            )
                        with output_cols[1]:
                            wip_scrap_units = st.number_input(
                                "Брак, шт.", min_value=0, max_value=1000000, value=0, step=1,
                                disabled=not immediate, key="wip_scrap_units",
                            )
                        with output_cols[2]:
                            if immediate and int(wip_produced_units or 0) > 0 and wip_total_meters > 0:
                                st.metric("Фактический выход", f"{float(wip_produced_units) / wip_total_meters:.2f} шт./м")
                        wip_issue_note = st.text_input("Примечание к партии", key="wip_issue_note", placeholder="Смена, станок, причина брака, оператор")
                        wip_issue_confirm = st.checkbox(
                            f"Подтверждаю списание {wip_total_meters:.3f} м материала «{wip_material_name}»",
                            key="wip_issue_confirm",
                            disabled=wip_total_meters <= 0,
                        )
                        if st.button(
                            "Провести выпуск заготовок" if immediate else "Выдать сырьё в НЗП",
                            type="primary", use_container_width=True, key="post_wip_issue",
                            disabled=not wip_issue_confirm or wip_total_meters <= 0 or (immediate and int(wip_produced_units or 0) <= 0),
                        ):
                            if immediate:
                                wip_result = post_wip_blank_batch(
                                    wip_batch_date, wip_material_name, wip_blank_type, wip_total_meters,
                                    int(wip_produced_units), int(wip_scrap_units), wip_issue_note,
                                )
                            else:
                                wip_result = issue_wip_material(
                                    wip_batch_date, wip_material_name, wip_blank_type, wip_total_meters, wip_issue_note,
                                )
                            if wip_result.get("errors"):
                                st.session_state["movement_flash"] = ("error", " | ".join(str(x) for x in wip_result.get("errors", [])))
                            else:
                                text = f"Сырьё списано: {float(wip_result.get('meters', 0) or 0):.3f} м"
                                if int(wip_result.get("units", 0) or 0) > 0:
                                    text += f"; заготовок оприходовано: {int(wip_result.get('units', 0) or 0)} шт."
                                else:
                                    text += "; партия оставлена открытой до подсчёта выпуска."
                                st.session_state["movement_flash"] = ("success", text)
                            st.cache_data.clear()
                            st.rerun()

                open_wip_batches = read_wip_blank_batches(active_only=True)
                open_wip_batches = open_wip_batches[open_wip_batches.get("status", pd.Series(dtype=str)).astype(str).eq("open")].copy() if not open_wip_batches.empty else pd.DataFrame()
                st.markdown("##### Закрыть ранее открытую партию")
                if open_wip_batches.empty:
                    st.success("Открытых партий без подсчитанного выпуска нет.")
                else:
                    open_labels = {
                        int(r["id"]): f"Партия #{int(r['id'])} · {r.get('batch_date','')} · {r.get('material_name','')} · {r.get('blank_type','')} · {float(r.get('issued_meters',0) or 0):.3f} м"
                        for _, r in open_wip_batches.iterrows()
                    }
                    close_wip_id = st.selectbox("Открытая партия", list(open_labels), format_func=lambda x: open_labels[int(x)], key="close_wip_batch_id")
                    close_row = open_wip_batches[open_wip_batches["id"].astype(int).eq(int(close_wip_id))].iloc[0]
                    close_cols = st.columns(3)
                    with close_cols[0]:
                        close_good_units = st.number_input("Фактически годных, шт.", min_value=1, max_value=1000000, value=1, step=1, key="close_wip_good")
                    with close_cols[1]:
                        close_scrap_units = st.number_input("Фактический брак, шт.", min_value=0, max_value=1000000, value=0, step=1, key="close_wip_scrap")
                    with close_cols[2]:
                        st.metric("Стоимость выданного сырья", money(float(close_row.get("material_cost_rub", 0) or 0)))
                    close_wip_note = st.text_input("Дополнение к примечанию", key="close_wip_note")
                    close_wip_confirm = st.checkbox("Подтверждаю фактический выпуск партии", key="close_wip_confirm")
                    if st.button("Закрыть партию и оприходовать заготовки", use_container_width=True, disabled=not close_wip_confirm, key="complete_wip_batch"):
                        close_result = complete_wip_blank_batch(int(close_wip_id), int(close_good_units), int(close_scrap_units), close_wip_note)
                        if close_result.get("errors"):
                            st.session_state["movement_flash"] = ("error", " | ".join(str(x) for x in close_result.get("errors", [])))
                        else:
                            st.session_state["movement_flash"] = ("success", f"Партия НЗП закрыта: {int(close_result.get('units', 0) or 0)} годных заготовок.")
                        st.cache_data.clear()
                        st.rerun()

            with wip_tabs[1]:
                st.markdown("##### Текущие остатки незавершённого производства")
                if wip_summary.empty:
                    st.info("Заготовки в НЗП ещё не оприходованы.")
                else:
                    summary_view = wip_summary.copy()
                    st.dataframe(
                        summary_view[["material_name", "blank_type", "remaining_units", "avg_unit_cost_rub", "remaining_cost_rub", "open_batches", "open_meters"]],
                        hide_index=True, use_container_width=True,
                        column_config={
                            "material_name": "Материал / цвет", "blank_type": "Тип заготовки",
                            "remaining_units": st.column_config.NumberColumn("Остаток, шт.", format="%d"),
                            "avg_unit_cost_rub": st.column_config.NumberColumn("Средняя стоимость, ₽/шт.", format="%.2f"),
                            "remaining_cost_rub": st.column_config.NumberColumn("Стоимость остатка, ₽", format="%.2f"),
                            "open_batches": st.column_config.NumberColumn("Открытых партий", format="%d"),
                            "open_meters": st.column_config.NumberColumn("Материал в открытых партиях, м", format="%.3f"),
                        },
                    )
                if wip_batches.empty:
                    st.info("Партий НЗП пока нет.")
                else:
                    batch_view = wip_batches.copy()
                    batch_view["batch_date"] = pd.to_datetime(batch_view["batch_date"], errors="coerce")
                    status_labels = {"open": "Материал выдан", "active": "Есть остаток", "depleted": "Израсходована", "reversed": "Отменена"}
                    batch_view["Статус"] = batch_view.get("status", "").astype(str).map(status_labels).fillna(batch_view.get("status", ""))
                    st.dataframe(
                        batch_view[["id", "batch_date", "material_name", "blank_type", "issued_meters", "produced_units", "scrap_units", "remaining_units", "unit_cost_rub", "remaining_cost_rub", "Статус", "note"]],
                        hide_index=True, use_container_width=True, height=430,
                        column_config={
                            "id": st.column_config.NumberColumn("Партия", format="%d"),
                            "batch_date": st.column_config.DateColumn("Дата", format="DD.MM.YYYY"),
                            "material_name": "Материал / цвет", "blank_type": "Тип заготовки",
                            "issued_meters": st.column_config.NumberColumn("Списано материала, м", format="%.3f"),
                            "produced_units": st.column_config.NumberColumn("Выпущено, шт.", format="%d"),
                            "scrap_units": st.column_config.NumberColumn("Брак, шт.", format="%d"),
                            "remaining_units": st.column_config.NumberColumn("Осталось, шт.", format="%d"),
                            "unit_cost_rub": st.column_config.NumberColumn("Себестоимость, ₽/шт.", format="%.2f"),
                            "remaining_cost_rub": st.column_config.NumberColumn("Стоимость остатка, ₽", format="%.2f"),
                            "note": "Примечание",
                        },
                    )
                    st.download_button(
                        "Скачать остатки и партии НЗП CSV",
                        data=batch_view.to_csv(index=False).encode("utf-8-sig"),
                        file_name=f"wip_blank_batches_{today_msk:%Y%m%d}.csv", mime="text/csv",
                    )

            with wip_tabs[2]:
                st.markdown("##### Упаковать комплекты из заготовок")
                st.warning(
                    "Для плановой смены предпочтительно заполнить фактические комплекты во вкладке «Сменное задание» и закрыть смену там. "
                    "Ручная комплектация предназначена для внепланового выпуска и не должна дублировать уже проведённое сменное задание."
                )
                cfg_pack = read_table("production_settings")
                catalog_pack = read_table("products_catalog")
                if cfg_pack.empty:
                    st.info("Нет настроенных производимых товаров.")
                else:
                    cfg_pack = cfg_pack.copy()
                    cfg_pack["enabled"] = pd.to_numeric(cfg_pack.get("enabled", 0), errors="coerce").fillna(0).astype(int)
                    cfg_pack = cfg_pack[cfg_pack["enabled"].eq(1)].copy()
                    if not catalog_pack.empty:
                        cfg_pack = cfg_pack.merge(catalog_pack[["nm_id", "product_name"]], on="nm_id", how="left")
                    else:
                        cfg_pack["product_name"] = ""
                    cfg_pack["pack_size"] = pd.to_numeric(cfg_pack.get("pack_size", 1), errors="coerce").fillna(1).clip(lower=1).astype(int)
                    cfg_pack["label"] = cfg_pack.apply(
                        lambda r: f"{r.get('supplier_article','')} · {r.get('product_name','')} · {r.get('material_name','')} · {r.get('blank_type','')} · {int(r.get('pack_size',1))} шт.", axis=1
                    )
                    pack_options = cfg_pack["nm_id"].astype(int).tolist()
                    if not pack_options:
                        st.info("Нет включённых производимых товаров.")
                    else:
                        pack_label_by_nm = dict(zip(cfg_pack["nm_id"].astype(int), cfg_pack["label"].astype(str)))
                        selected_pack_nm = st.selectbox("Товар / комплект", pack_options, format_func=lambda x: pack_label_by_nm.get(int(x), str(x)), key="manual_wip_pack_nm")
                        pack_row = cfg_pack[cfg_pack["nm_id"].astype(int).eq(int(selected_pack_nm))].iloc[0]
                        pack_material = str(pack_row.get("material_name", "") or "").strip()
                        pack_blank = str(pack_row.get("blank_type", "") or "").strip()
                        pack_size = int(pack_row.get("pack_size", 1) or 1)
                        available_blanks = 0
                        if not wip_summary.empty:
                            match_summary = wip_summary[
                                wip_summary.get("material_name", "").astype(str).str.strip().eq(pack_material)
                                & wip_summary.get("blank_type", "").astype(str).str.strip().eq(pack_blank)
                            ]
                            available_blanks = int(pd.to_numeric(match_summary.get("remaining_units", 0), errors="coerce").fillna(0).sum()) if not match_summary.empty else 0
                        max_sets = available_blanks // max(1, pack_size)
                        pack_info_cols = st.columns(3)
                        with pack_info_cols[0]: kpi_card("Доступно заготовок", num(available_blanks), f"{pack_material} · {pack_blank}")
                        with pack_info_cols[1]: kpi_card("Размер комплекта", num(pack_size), "Штук в продаваемой единице")
                        with pack_info_cols[2]: kpi_card("Можно упаковать", num(max_sets), "Комплектов без отрицательного НЗП")
                        manual_pack_qty = st.number_input("Фактически упаковано комплектов", min_value=0, max_value=max(0, int(max_sets)), value=0, step=1, key="manual_wip_pack_qty")
                        required_for_pack = int(manual_pack_qty) * pack_size
                        st.caption(f"Будет списано {required_for_pack} отдельных заготовок. Сырьё повторно не списывается.")
                        manual_pack_date = st.date_input("Дата комплектации", value=today_msk, key="manual_wip_pack_date")
                        manual_pack_note = st.text_input("Примечание к комплектации", key="manual_wip_pack_note")
                        manual_pack_confirm = st.checkbox(f"Подтверждаю упаковку {int(manual_pack_qty)} комплектов", key="manual_wip_pack_confirm", disabled=int(manual_pack_qty) <= 0)
                        if st.button("Оприходовать комплекты из НЗП", type="primary", use_container_width=True, key="post_manual_wip_packaging", disabled=not manual_pack_confirm or int(manual_pack_qty) <= 0):
                            pack_result = post_manual_wip_packaging(int(selected_pack_nm), int(manual_pack_qty), manual_pack_date, manual_pack_note)
                            if pack_result.get("errors"):
                                st.session_state["movement_flash"] = ("error", " | ".join(str(x) for x in pack_result.get("errors", [])))
                            else:
                                st.session_state["movement_flash"] = (
                                    "success",
                                    f"Оприходовано {int(pack_result.get('units',0) or 0)} комплектов; списано {int(pack_result.get('wip_units',0) or 0)} заготовок; стоимость партии {money(float(pack_result.get('cost_rub',0) or 0))}."
                                )
                            st.cache_data.clear()
                            st.rerun()

            with wip_tabs[3]:
                st.markdown("##### Движения незавершённого производства")
                wip_movements = read_inventory_movements(1000)
                wip_types = ["wip_material_issue", "wip_blank_receipt", "production_receipt_wip"]
                wip_movements = wip_movements[wip_movements.get("movement_type", pd.Series(dtype=str)).astype(str).isin(wip_types)].copy() if not wip_movements.empty else pd.DataFrame()
                if wip_movements.empty:
                    st.info("Движений НЗП пока нет.")
                else:
                    wip_movement_labels = {
                        "wip_material_issue": "Выдача сырья в НЗП",
                        "wip_blank_receipt": "Оприходование заготовок",
                        "production_receipt_wip": "Комплектация готового товара",
                    }
                    wip_movements["Операция"] = wip_movements["movement_type"].map(wip_movement_labels).fillna(wip_movements["movement_type"])
                    wip_movements["Дата"] = pd.to_datetime(wip_movements["movement_date"], errors="coerce")
                    wip_movements["Состояние"] = wip_movements.apply(lambda r: "Отменена" if pd.notna(r.get("reversed_at")) else "Проведена", axis=1)
                    st.dataframe(
                        wip_movements[["id", "Дата", "Операция", "Состояние", "supplier_article", "product_name", "quantity", "material_name", "material_delta", "material_cost_rub", "goods_cost_rub", "note", "created_at"]],
                        hide_index=True, use_container_width=True, height=420,
                        column_config={
                            "id": st.column_config.NumberColumn("№", format="%d"), "Дата": st.column_config.DateColumn(format="DD.MM.YYYY"),
                            "supplier_article": "Артикул продавца", "product_name": "Объект",
                            "quantity": st.column_config.NumberColumn("Количество", format="%d"),
                            "material_name": "Материал / цвет", "material_delta": st.column_config.NumberColumn("Изменение сырья, м", format="%.3f"),
                            "material_cost_rub": st.column_config.NumberColumn("Стоимость сырья / НЗП, ₽", format="%.2f"),
                            "goods_cost_rub": st.column_config.NumberColumn("Стоимость выпуска, ₽", format="%.2f"),
                            "note": "Примечание", "created_at": "Записано",
                        },
                    )
                    wip_cancel = wip_movements[wip_movements["reversed_at"].isna()].copy()
                    if not wip_cancel.empty:
                        wip_cancel_labels = {
                            int(r["id"]): f"№{int(r['id'])} · {wip_movement_labels.get(str(r['movement_type']), str(r['movement_type']))} · {r.get('material_name') or r.get('supplier_article') or ''}"
                            for _, r in wip_cancel.iterrows()
                        }
                        wip_cancel_id = st.selectbox("Отменить ошибочное движение НЗП", list(wip_cancel_labels), format_func=lambda x: wip_cancel_labels[int(x)], key="cancel_wip_movement")
                        st.caption("Отмена разрешена только в обратном порядке: сначала комплектация, затем выпуск заготовок, затем выдача сырья.")
                        cancel_wip_confirm = st.checkbox("Подтверждаю отмену выбранного движения", key="confirm_cancel_wip")
                        if st.button("Отменить движение НЗП", disabled=not cancel_wip_confirm, key="undo_wip_movement"):
                            undo_result = undo_inventory_movement(int(wip_cancel_id))
                            st.session_state["movement_flash"] = ("success" if undo_result.get("ok") else "error", str(undo_result.get("message", "")))
                            st.cache_data.clear()
                            st.rerun()

                wip_allocations = read_wip_blank_allocations(500)
                if not wip_allocations.empty:
                    with st.expander("Показать FIFO-списания заготовок по партиям"):
                        st.dataframe(
                            wip_allocations[["id", "movement_id", "batch_id", "batch_date", "material_name", "blank_type", "units", "amount_rub", "supplier_article", "product_name", "status", "created_at"]],
                            hide_index=True, use_container_width=True,
                            column_config={
                                "id": st.column_config.NumberColumn("Распределение", format="%d"),
                                "movement_id": st.column_config.NumberColumn("Движение", format="%d"),
                                "batch_id": st.column_config.NumberColumn("Партия НЗП", format="%d"),
                                "batch_date": "Дата партии", "material_name": "Материал / цвет", "blank_type": "Тип заготовки",
                                "units": st.column_config.NumberColumn("Списано, шт.", format="%d"),
                                "amount_rub": st.column_config.NumberColumn("Стоимость, ₽", format="%.2f"),
                                "supplier_article": "Артикул продавца", "product_name": "Товар", "status": "Статус", "created_at": "Записано",
                            },
                        )


        operational_export_columns = [
            "Оперативный приоритет", "Артикул WB", "Артикул продавца", "Товар", "Статус",
            "Остаток", "Продаж/день", "Запас WB, дней", "Дата обнуления до поставки",
            "Ближайшее пополнение WB", "Ожидаемый разрыв, дней", "Закрыть разрыв, компл.",
            "Цель первой отгрузки, компл.", "Готово к отгрузке, компл.",
            "Аварийная партия по плану, компл.", "FBS / ускоренная схема",
            "Реклама", "Доля рекламы, %", "Ограничить рекламу до", "Рекламное действие",
            "Вернуть обычный режим", "Действия сейчас"
        ]
        operational_export = plan[operational_export_columns].copy().sort_values(
            ["Оперативный приоритет", "Ожидаемый разрыв, дней", "Запас WB, дней"],
            ascending=[True, False, True], na_position="last"
        )
        st.download_button(
            "Скачать оперативный план CSV",
            data=operational_export.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"operational_action_plan_{start:%Y%m%d}_{end:%Y%m%d}.csv",
            mime="text/csv",
        )

        if norm_coverage < 100:
            st.warning("Для части товаров не заполнена норма материала. Количество рассчитано, но общий расход материала занижен.")

        st.markdown("### Очерёдность производства")
        plan_columns = [
            "Приоритет", "Артикул продавца", "Товар", "Статус", "Остаток",
            "Готово учтено", "В пути учтено", "Дата прибытия", "Всего с поставками",
            "Тип заготовки", "Материал / цвет", "Штук в комплекте", "Продаж/день",
            "Запас WB, дней", "Запас с готовым, дней", "Запас с поставками, дней", "Целевой запас, дней",
            "Потребность, компл.", "Мин. партия, компл.", "Рекомендовано, компл.",
            "Запланировано по наличию сырья, компл.", "Заблокировано сырьём, компл.",
            "Штук к производству", "Нужно материала, м", "Крайний срок производства",
            "Плановая дата первого выпуска", "Плановая дата первого пополнения WB", "Плановая дата завершения",
            "Ближайшее пополнение WB", "Ожидаемый разрыв, дней", "Риск отгрузки",
            "Закрыть разрыв, компл.", "Цель первой отгрузки, компл.",
            "FBS / ускоренная схема", "Реклама", "Доля рекламы, %", "Рекламное действие",
            "Не успеваем до обнуления", "Расчётная прибыль", "Расчётная маржа, %",
            "Изготовить", "Отгрузить", "Действие"
        ]
        st.dataframe(
            plan[plan_columns], hide_index=True, use_container_width=True, height=600,
            column_config={
                "Остаток": st.column_config.NumberColumn(format="%.0f"),
                "Готово учтено": st.column_config.NumberColumn(format="%.0f"),
                "В пути учтено": st.column_config.NumberColumn(format="%.0f"),
                "Дата прибытия": st.column_config.DateColumn(format="DD.MM.YYYY"),
                "Всего с поставками": st.column_config.NumberColumn(format="%.0f"),
                "Продаж/день": st.column_config.NumberColumn(format="%.2f"),
                "Запас WB, дней": st.column_config.NumberColumn(format="%.1f"),
                "Запас с готовым, дней": st.column_config.NumberColumn(format="%.1f"),
                "Запас с поставками, дней": st.column_config.NumberColumn(format="%.1f"),
                "Потребность, компл.": st.column_config.NumberColumn(format="%.0f"),
                "Мин. партия, компл.": st.column_config.NumberColumn(format="%.0f"),
                "Рекомендовано, компл.": st.column_config.NumberColumn(format="%.0f"),
                "Штук к производству": st.column_config.NumberColumn(format="%.0f"),
                "Нужно материала, м": st.column_config.NumberColumn(format="%.1f"),
                "Крайний срок производства": st.column_config.DateColumn(format="DD.MM.YYYY"),
                "Плановая дата первого выпуска": st.column_config.DateColumn(format="DD.MM.YYYY"),
                "Плановая дата первого пополнения WB": st.column_config.DateColumn(format="DD.MM.YYYY"),
                "Плановая дата завершения": st.column_config.DateColumn(format="DD.MM.YYYY"),
                "Ближайшее пополнение WB": st.column_config.DateColumn(format="DD.MM.YYYY"),
                "Ожидаемый разрыв, дней": st.column_config.NumberColumn(format="%.0f"),
                "Закрыть разрыв, компл.": st.column_config.NumberColumn(format="%.0f"),
                "Цель первой отгрузки, компл.": st.column_config.NumberColumn(format="%.0f"),
                "Реклама": st.column_config.NumberColumn(format="%.0f ₽"),
                "Доля рекламы, %": st.column_config.NumberColumn(format="%.1f%%"),
                "Не успеваем до обнуления": st.column_config.CheckboxColumn("Риск срока"),
                "Расчётная прибыль": st.column_config.NumberColumn(format="%.0f ₽"),
                "Расчётная маржа, %": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )
        st.download_button(
            "Скачать план производства CSV",
            data=plan[plan_columns].to_csv(index=False).encode("utf-8-sig"),
            file_name=f"production_plan_{start:%Y%m%d}_{end:%Y%m%d}.csv",
            mime="text/csv",
        )

