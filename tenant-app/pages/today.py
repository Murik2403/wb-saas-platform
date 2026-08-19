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
    date,
)
from db import (
    fifo_reconciliation_status,
    get_production_capacity,
    read_procurement_orders,
    read_table,
)


def render(ctx: dict) -> None:
    backup_info = ctx['backup_info']
    data = ctx['data']
    settings = ctx['settings']
    sync_info = ctx['sync_info']
    today_msk = ctx['today_msk']

    st.markdown("### Сегодня")
    capacity_today = get_production_capacity()
    emergency_days_today = max(1, int(capacity_today.get("emergency_cover_days", 14) or 14))
    lead_days_today = max(0, int(capacity_today.get("fulfillment_lead_days", 10) or 10))
    products_today = data.products.copy() if not data.products.empty else pd.DataFrame()
    critical_stock = pd.DataFrame()
    if not products_today.empty and "Запас, дней" in products_today.columns:
        critical_stock = products_today[pd.to_numeric(products_today["Запас, дней"], errors="coerce").fillna(9999) <= emergency_days_today].copy().sort_values("Запас, дней")

    execution_today = read_table("execution_tasks")
    movements_today = read_table("inventory_movements")
    pipeline_today = read_table("product_pipeline")
    production_cfg_today = read_table("production_settings")
    raw_inventory_today = read_table("material_inventory_color")
    procurement_orders_today = read_procurement_orders()
    overdue_procurement_today = pd.DataFrame()
    payment_due_today = pd.DataFrame()
    if not procurement_orders_today.empty:
        procurement_orders_today = procurement_orders_today.copy()
        procurement_orders_today["expected_dt"] = pd.to_datetime(procurement_orders_today["expected_date"], errors="coerce").dt.date
        procurement_orders_today["payment_due_dt"] = pd.to_datetime(procurement_orders_today["payment_due_date"], errors="coerce").dt.date
        active_proc_mask = ~procurement_orders_today["status"].isin(["Получено", "Отменено"])
        overdue_procurement_today = procurement_orders_today[active_proc_mask & procurement_orders_today["expected_dt"].notna() & (procurement_orders_today["expected_dt"] < today_msk)].copy()
        outstanding_today = pd.to_numeric(procurement_orders_today.get("outstanding_amount", 0), errors="coerce").fillna(0)
        unpaid_mask = (~procurement_orders_today["status"].isin(["Получено", "Отменено"])) & outstanding_today.gt(0.01)
        payment_due_today = procurement_orders_today[unpaid_mask & procurement_orders_today["payment_due_dt"].notna() & (procurement_orders_today["payment_due_dt"] <= today_msk)].copy()

    # v3.8: classify items as own production or purchased and build a compact
    # replenishment/procurement view for the Today page.
    cfg_today = production_cfg_today.copy()
    if not cfg_today.empty:
        cfg_today["nm_id"] = pd.to_numeric(cfg_today["nm_id"], errors="coerce").fillna(0).astype(int)
        cfg_today["enabled"] = pd.to_numeric(cfg_today.get("enabled", 0), errors="coerce").fillna(0).astype(int)
        for col, default in [("material_per_unit", 0.0), ("target_days", 21), ("min_batch", 1), ("pack_size", 4)]:
            cfg_today[col] = pd.to_numeric(cfg_today.get(col, default), errors="coerce").fillna(default)
        cfg_today["material_name"] = cfg_today.get("material_name", "").fillna("").astype(str).str.strip()
    enabled_cfg_today = cfg_today[cfg_today.get("enabled", pd.Series(dtype=int)).eq(1)].copy() if not cfg_today.empty else pd.DataFrame()
    own_nm_today = set(enabled_cfg_today.get("nm_id", pd.Series(dtype=int)).astype(int).tolist()) if not enabled_cfg_today.empty else set()

    pipeline_map_today: dict[int, dict] = {}
    if not pipeline_today.empty:
        pipeline_work = pipeline_today.copy()
        pipeline_work["nm_id"] = pd.to_numeric(pipeline_work["nm_id"], errors="coerce").fillna(0).astype(int)
        for _, pipeline_row in pipeline_work.iterrows():
            pipeline_map_today[int(pipeline_row["nm_id"])] = pipeline_row.to_dict()

    production_need_rows: list[dict] = []
    purchase_product_rows: list[dict] = []
    if not products_today.empty:
        cfg_lookup = enabled_cfg_today.set_index("nm_id").to_dict("index") if not enabled_cfg_today.empty else {}
        for _, product_row in products_today.iterrows():
            try:
                nm_value = int(float(product_row.get("Артикул WB", 0) or 0))
            except (TypeError, ValueError):
                nm_value = 0
            if nm_value <= 0:
                continue
            article = str(product_row.get("Артикул продавца", "") or "")
            product_name = str(product_row.get("Товар", "") or "")
            stock_value = max(0.0, float(product_row.get("Остаток", 0) or 0))
            avg_daily_value = max(0.0, float(product_row.get("Продаж/день", 0) or 0))
            days_value = product_row.get("Запас, дней", math.nan)
            try:
                days_value = float(days_value)
            except (TypeError, ValueError):
                days_value = math.nan
            pipeline_row = pipeline_map_today.get(nm_value, {})
            ready_value = max(0, int(float(pipeline_row.get("ready_units", 0) or 0))) if int(pipeline_row.get("local_known", 0) or 0) else 0
            inbound_value = max(0, int(float(pipeline_row.get("inbound_units", 0) or 0))) if int(pipeline_row.get("inbound_known", 0) or 0) else 0
            if nm_value in own_nm_today:
                cfg_row = cfg_lookup.get(nm_value, {})
                target_days_value = max(1, int(float(cfg_row.get("target_days", 21) or 21)))
                min_batch_value = max(1, int(float(cfg_row.get("min_batch", 1) or 1)))
                material_rate_value = max(0.0, float(cfg_row.get("material_per_unit", 0) or 0))
                need_raw = max(avg_daily_value * target_days_value - stock_value - ready_value - inbound_value, 0.0)
                need_units = ceil_to_batch(need_raw, min_batch_value)
                production_need_rows.append({
                    "Артикул WB": nm_value, "Артикул продавца": article, "Товар": product_name,
                    "Материал / цвет": str(cfg_row.get("material_name", "") or "").strip(),
                    "Нужно произвести, компл.": need_units, "Материал на комплект, м": material_rate_value,
                    "Нужно материала, м": need_units * material_rate_value, "Остаток WB": stock_value,
                    "Продаж/день": avg_daily_value, "Запас WB, дней": days_value,
                    "Готово": ready_value, "В пути": inbound_value,
                })
            elif pd.notna(days_value) and days_value <= emergency_days_today:
                target_purchase_days = max(21, emergency_days_today)
                order_qty = int(math.ceil(max(avg_daily_value * target_purchase_days - stock_value - inbound_value, 0.0)))
                purchase_product_rows.append({
                    "Артикул WB": nm_value, "Артикул продавца": article, "Товар": product_name,
                    "Остаток WB": int(round(stock_value)), "Продаж/день": avg_daily_value,
                    "Запас WB, дней": days_value, "В пути": inbound_value,
                    "Ориентировочно заказать, шт.": order_qty,
                    "Действие": "Срочно заказать у поставщика; ограничить рекламу до пополнения" if days_value <= lead_days_today else "Заказать у поставщика до целевого запаса",
                })
    production_need_today = pd.DataFrame(production_need_rows)
    purchase_products_today = pd.DataFrame(purchase_product_rows)

    material_procurement_today = pd.DataFrame()
    if not production_need_today.empty:
        material_procurement_today = production_need_today[production_need_today["Материал / цвет"].str.strip().ne("")].groupby("Материал / цвет", as_index=False).agg({
            "Нужно материала, м": "sum",
            "Артикул продавца": lambda values: ", ".join(sorted(set(str(v) for v in values if str(v).strip()))),
        })
        material_procurement_today["material_key"] = material_procurement_today["Материал / цвет"].apply(material_key)
        if not raw_inventory_today.empty:
            raw_join = raw_inventory_today[["material_key", "balance_known", "full_rolls", "partial_meters", "roll_length"]].copy()
            material_procurement_today = material_procurement_today.merge(raw_join, on="material_key", how="left")
        for col, default in [("balance_known", 0), ("full_rolls", 0), ("partial_meters", 0.0), ("roll_length", 25.5)]:
            if col not in material_procurement_today.columns:
                material_procurement_today[col] = default
            material_procurement_today[col] = pd.to_numeric(material_procurement_today[col], errors="coerce").fillna(default)
        material_procurement_today["Остаток указан"] = material_procurement_today["balance_known"].astype(int).eq(1)
        material_procurement_today["На складе, м"] = material_procurement_today["full_rolls"] * material_procurement_today["roll_length"] + material_procurement_today["partial_meters"]
        material_procurement_today["Не хватает, м"] = (material_procurement_today["Нужно материала, м"] - material_procurement_today["На складе, м"]).clip(lower=0)
        material_procurement_today["Рулонов докупить"] = material_procurement_today.apply(
            lambda row: int(math.ceil(float(row["Не хватает, м"]) / max(float(row["roll_length"]), 0.1))) if bool(row["Остаток указан"]) and float(row["Не хватает, м"]) > 0 else 0, axis=1
        )
        material_procurement_today["Статус"] = material_procurement_today.apply(
            lambda row: "Остаток не указан" if not bool(row["Остаток указан"]) else ("Закупить" if float(row["Не хватает, м"]) > 0.01 else "Достаточно"), axis=1
        )
    material_buy_today = material_procurement_today[material_procurement_today.get("Статус", pd.Series(dtype=str)).eq("Закупить")].copy() if not material_procurement_today.empty else pd.DataFrame()
    blocked_products_today = pd.DataFrame()
    if not production_need_today.empty and not material_buy_today.empty:
        shortage_names = set(material_buy_today["Материал / цвет"].astype(str))
        blocked_products_today = production_need_today[production_need_today["Материал / цвет"].isin(shortage_names) & production_need_today["Нужно произвести, компл."].gt(0)].copy()
        shortage_lookup = material_buy_today.set_index("Материал / цвет")["Не хватает, м"].to_dict()
        rolls_lookup = material_buy_today.set_index("Материал / цвет")["Рулонов докупить"].to_dict()
        blocked_products_today["Дефицит материала, м"] = blocked_products_today["Материал / цвет"].map(shortage_lookup).fillna(0.0)
        blocked_products_today["Рулонов докупить"] = blocked_products_today["Материал / цвет"].map(rolls_lookup).fillna(0).astype(int)

    if not execution_today.empty:
        execution_today["task_date_dt"] = pd.to_datetime(execution_today["task_date"], errors="coerce").dt.date
        for col in ["actual_units", "planned_units"]:
            execution_today[col] = pd.to_numeric(execution_today[col], errors="coerce").fillna(0).astype(int)
    active_movements = movements_today[movements_today["reversed_at"].isna()].copy() if not movements_today.empty and "reversed_at" in movements_today.columns else movements_today.copy()
    active_types = set(zip(active_movements.get("movement_type", pd.Series(dtype=str)).fillna("").astype(str), active_movements.get("source_task_key", pd.Series(dtype=str)).fillna("").astype(str)))
    production_tasks = execution_today[execution_today.get("task_type", pd.Series(dtype=str)).eq("production")].copy() if not execution_today.empty else pd.DataFrame()
    prod_today = production_tasks[production_tasks.get("task_date_dt", pd.Series(dtype=object)).eq(today_msk)].copy() if not production_tasks.empty else pd.DataFrame()
    future_production = production_tasks[(production_tasks.get("task_date_dt", pd.Series(dtype=object)) >= today_msk) & ~production_tasks.get("status", pd.Series(dtype=str)).isin(["Передано на отгрузку", "Отгружено", "Принято WB", "Закрыто"])].copy() if not production_tasks.empty else pd.DataFrame()
    next_shift_date = future_production["task_date_dt"].min() if not future_production.empty else None
    if next_shift_date is not None and pd.isna(next_shift_date):
        next_shift_date = None
    next_shift = future_production[future_production["task_date_dt"].eq(next_shift_date)].copy() if next_shift_date is not None else pd.DataFrame()
    shift_tasks = prod_today.copy() if not prod_today.empty else next_shift.copy()
    displayed_shift_date = today_msk if not prod_today.empty else next_shift_date
    overdue = execution_today[(execution_today.get("task_date_dt", pd.Series(dtype=object)) < today_msk) & ~execution_today.get("status", pd.Series(dtype=str)).isin(["Изготовлено", "Упаковано", "Передано на отгрузку", "Отгружено", "Принято WB", "Закрыто"])].copy() if not execution_today.empty else pd.DataFrame()
    unposted_dispatch = execution_today[(execution_today.get("task_type", pd.Series(dtype=str)) == "dispatch") & (execution_today.get("status", pd.Series(dtype=str)) == "Отгружено") & (execution_today.get("actual_units", pd.Series(dtype=int)) > 0) & ~execution_today.get("task_key", pd.Series(dtype=str)).astype(str).map(lambda key: ("dispatch", key) in active_types)].copy() if not execution_today.empty else pd.DataFrame()
    pending_receipt = execution_today[(execution_today.get("task_type", pd.Series(dtype=str)) == "dispatch") & execution_today.get("status", pd.Series(dtype=str)).isin(["Принято WB", "Закрыто"]) & (execution_today.get("actual_units", pd.Series(dtype=int)) > 0) & ~execution_today.get("task_key", pd.Series(dtype=str)).astype(str).map(lambda key: ("wb_receipt", key) in active_types)].copy() if not execution_today.empty else pd.DataFrame()
    ready_total = int(pd.to_numeric(pipeline_today.get("ready_units", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not pipeline_today.empty else 0
    inbound_total = int(pd.to_numeric(pipeline_today.get("inbound_units", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not pipeline_today.empty else 0
    planned_shift = int(pd.to_numeric(shift_tasks.get("planned_units", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not shift_tasks.empty else 0
    actual_shift = int(pd.to_numeric(shift_tasks.get("actual_units", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not shift_tasks.empty else 0

    shift_material = pd.DataFrame(); material_shortages = pd.DataFrame(); material_unknown = pd.DataFrame()
    if not shift_tasks.empty and not production_cfg_today.empty:
        shift_material = shift_tasks.merge(production_cfg_today[["nm_id", "material_name", "material_per_unit"]], on="nm_id", how="left")
        shift_material["material_name"] = shift_material["material_name"].fillna("").astype(str)
        shift_material["material_per_unit"] = pd.to_numeric(shift_material["material_per_unit"], errors="coerce").fillna(0.0)
        shift_material["Потребность, м"] = shift_material["planned_units"] * shift_material["material_per_unit"]
        shift_material = shift_material.groupby("material_name", as_index=False).agg({"Потребность, м": "sum"})
        shift_material = shift_material[shift_material["material_name"].str.strip().ne("")].copy()
        shift_material["material_key"] = shift_material["material_name"].apply(material_key)
        if not raw_inventory_today.empty:
            shift_material = shift_material.merge(raw_inventory_today[["material_key", "balance_known", "full_rolls", "partial_meters", "roll_length"]], on="material_key", how="left")
        for col, default in [("balance_known",0),("full_rolls",0),("partial_meters",0.0),("roll_length",25.5)]:
            if col not in shift_material.columns: shift_material[col]=default
            shift_material[col]=pd.to_numeric(shift_material[col],errors="coerce").fillna(default)
        shift_material["Остаток указан"] = shift_material["balance_known"].astype(int).eq(1)
        shift_material["На складе, м"] = shift_material["full_rolls"]*shift_material["roll_length"]+shift_material["partial_meters"]
        shift_material["После смены, м"] = shift_material["На складе, м"]-shift_material["Потребность, м"]
        shift_material["Статус"] = shift_material.apply(lambda r: "Остаток не указан" if not bool(r["Остаток указан"]) else ("Не хватает" if float(r["После смены, м"]) < -0.01 else ("Критический остаток" if float(r["После смены, м"]) < float(r["roll_length"])*0.25 else "Достаточно")), axis=1)
        material_shortages=shift_material[shift_material["Статус"].eq("Не хватает")].copy(); material_unknown=shift_material[shift_material["Статус"].eq("Остаток не указан")].copy()

    shift_material_covered_units = 0
    if not shift_tasks.empty and not production_cfg_today.empty:
        coverage_tasks = shift_tasks.merge(
            production_cfg_today[["nm_id", "material_name", "material_per_unit"]],
            on="nm_id", how="left"
        )
        coverage_tasks["planned_units"] = pd.to_numeric(coverage_tasks.get("planned_units", 0), errors="coerce").fillna(0).astype(int)
        coverage_tasks["material_per_unit"] = pd.to_numeric(coverage_tasks.get("material_per_unit", 0), errors="coerce").fillna(0.0)
        coverage_tasks["material_name"] = coverage_tasks.get("material_name", "").fillna("").astype(str).str.strip()
        raw_available: dict[str, float | None] = {}
        if not raw_inventory_today.empty:
            for _, raw_row in raw_inventory_today.iterrows():
                key = str(raw_row.get("material_key", "") or "")
                known = int(raw_row.get("balance_known", 0) or 0) == 1
                if known:
                    raw_available[key] = max(0.0, float(raw_row.get("full_rolls", 0) or 0) * float(raw_row.get("roll_length", 25.5) or 25.5) + float(raw_row.get("partial_meters", 0) or 0))
                else:
                    raw_available[key] = None
        for _, task_row in coverage_tasks.iterrows():
            planned = max(0, int(task_row.get("planned_units", 0) or 0))
            rate = max(0.0, float(task_row.get("material_per_unit", 0) or 0))
            key = material_key(task_row.get("material_name", ""))
            available = raw_available.get(key)
            if planned <= 0 or rate <= 0 or available is None:
                continue
            covered = min(planned, int(math.floor((available + 1e-9) / rate)))
            shift_material_covered_units += max(0, covered)
            raw_available[key] = max(0.0, available - covered * rate)

    sync_stale=False; sync_age_text="Нет данных"
    if sync_info:
        raw_sync=sync_info.get("finished_at") or sync_info.get("started_at")
        try:
            sync_dt=pd.Timestamp(raw_sync); sync_dt=sync_dt.tz_localize("Europe/Moscow") if sync_dt.tzinfo is None else sync_dt.tz_convert("Europe/Moscow")
            age_minutes=max(0,int((pd.Timestamp.now(tz="Europe/Moscow")-sync_dt).total_seconds()//60)); sync_stale=age_minutes>max(90,int(settings.get("sync_interval_minutes",30))*3); sync_age_text=f"{age_minutes} мин. назад"
        except Exception: sync_age_text=str(raw_sync)
    backup_stale=False; backup_age_text="Нет копии"
    if backup_info:
        try:
            backup_dt=pd.Timestamp(backup_info["modified_at"]); age_hours=max(0,int((pd.Timestamp.now().tz_localize(None)-backup_dt.tz_localize(None)).total_seconds()//3600)); backup_stale=age_hours>24; backup_age_text=f"{age_hours} ч. назад"
        except Exception: backup_age_text=str(backup_info.get("modified_at",""))

    action_rows=[]
    material_shortage_lookup_today = material_buy_today.set_index("Материал / цвет").to_dict("index") if not material_buy_today.empty else {}
    production_cfg_lookup_today = enabled_cfg_today.set_index("nm_id").to_dict("index") if not enabled_cfg_today.empty else {}
    purchase_lookup_today = purchase_products_today.set_index("Артикул WB").to_dict("index") if not purchase_products_today.empty else {}
    for _,row in critical_stock.iterrows():
        days=float(row.get("Запас, дней",0) or 0)
        try:
            nm_value = int(float(row.get("Артикул WB", 0) or 0))
        except (TypeError, ValueError):
            nm_value = 0
        article = row.get("Артикул продавца","")
        priority = "Критично" if days<=lead_days_today else "Высокий"
        if nm_value in own_nm_today:
            cfg_row = production_cfg_lookup_today.get(nm_value, {})
            material_name_value = str(cfg_row.get("material_name", "") or "").strip()
            material_shortage = material_shortage_lookup_today.get(material_name_value)
            if material_shortage and float(material_shortage.get("Не хватает, м", 0) or 0) > 0:
                missing_meters = float(material_shortage.get("Не хватает, м", 0) or 0)
                rolls = int(material_shortage.get("Рулонов докупить", 0) or 0)
                action_rows.append({
                    "Приоритет":"Критично", "Тип":"Собственное производство", "Артикул продавца":article,
                    "Сигнал":f"Запас {days:.1f} дн.; производство заблокировано сырьём",
                    "Действие":f"Закупить {material_name_value}: не хватает {missing_meters:.1f} м ({rolls} рул.); после поступления поставить в смену",
                })
            else:
                action_rows.append({
                    "Приоритет":priority, "Тип":"Собственное производство", "Артикул продавца":article,
                    "Сигнал":f"Запас {days:.1f} дн.",
                    "Действие":"Ускорить производство/отгрузку; проверить FBS" if days<=lead_days_today else "Поставить в ближайшую смену",
                })
        else:
            purchase_row = purchase_lookup_today.get(nm_value, {})
            order_qty = int(purchase_row.get("Ориентировочно заказать, шт.", 0) or 0)
            action_rows.append({
                "Приоритет":priority, "Тип":"Закупаемый товар", "Артикул продавца":article,
                "Сигнал":f"Запас {days:.1f} дн.; товар не производится",
                "Действие":f"Заказать у поставщика ориентировочно {order_qty} шт.; ограничить рекламу до пополнения" if order_qty > 0 else "Проверить закупку у поставщика и ограничить рекламу",
            })
    for _,row in overdue.iterrows(): action_rows.append({"Приоритет":"Критично","Тип":"Просрочено","Артикул продавца":row.get("supplier_article",""),"Сигнал":f"Задание от {row.get('task_date','')}","Действие":"Обновить факт и статус либо перенести задание"})
    for _,row in unposted_dispatch.iterrows(): action_rows.append({"Приоритет":"Критично","Тип":"Отгрузка","Артикул продавца":row.get("supplier_article",""),"Сигнал":"Статус «Отгружено», движение не проведено","Действие":"Открыть Производство → Отгрузки и провести"})
    for _,row in pending_receipt.iterrows(): action_rows.append({"Приоритет":"Высокий","Тип":"Приёмка WB","Артикул продавца":row.get("supplier_article",""),"Сигнал":"Приёмка отмечена, локальный учёт не закрыт","Действие":"Зафиксировать приёмку WB"})
    for _,row in material_buy_today.iterrows():
        action_rows.append({
            "Приоритет":"Критично", "Тип":"Закупка сырья", "Артикул продавца":"",
            "Сигнал":f"{row.get('Материал / цвет','')}: не хватает {float(row.get('Не хватает, м',0)):.1f} м",
            "Действие":f"Докупить {int(row.get('Рулонов докупить',0) or 0)} рул.; затронуты: {row.get('Артикул продавца','')}",
        })
    for _, row in overdue_procurement_today.iterrows():
        action_rows.append({
            "Приоритет":"Критично", "Тип":"Закупка просрочена", "Артикул продавца":"",
            "Сигнал":f"{row.get('order_number','')}: ожидалась {row.get('expected_date','')}",
            "Действие":"Связаться с поставщиком и обновить срок во вкладке «Закупки»",
        })
    for _, row in payment_due_today.iterrows():
        action_rows.append({
            "Приоритет":"Высокий", "Тип":"Оплата поставщику", "Артикул продавца":"",
            "Сигнал":f"{row.get('order_number','')}: к оплате {money(float(row.get('outstanding_amount',0) or 0))}",
            "Действие":"Провести оплату или изменить срок во вкладке «Закупки»",
        })
    if sync_stale: action_rows.append({"Приоритет":"Высокий","Тип":"Данные","Артикул продавца":"","Сигнал":"Синхронизация устарела","Действие":"Выполнить синхронизацию WB"})
    if backup_stale: action_rows.append({"Приоритет":"Средний","Тип":"Резервная копия","Артикул продавца":"","Сигнал":"Копия старше суток","Действие":"Создать ручную резервную копию"})
    try:
        fifo_control_today = fifo_reconciliation_status()
        if int(fifo_control_today.get("current_lines", 0) or 0) > 0:
            blocked_fifo = int(fifo_control_today.get("current_blocked_units", 0) or 0)
            action_rows.append({
                "Приоритет":"Высокий", "Тип":"Сверка FIFO", "Артикул продавца":"",
                "Сигнал":f"Расхождения по {int(fifo_control_today.get('current_articles',0) or 0)} артикулам: +{int(fifo_control_today.get('current_added',0) or 0)} / -{int(fifo_control_today.get('current_removed',0) or 0)} ед."
                         + (f"; {blocked_fifo} ед. заблокированы как WB-диагностика" if blocked_fifo else ""),
                "Действие": (
                    "Открыть Остатки → Сверка FIFO; WB-расхождения не списывать, проверить возможное внешнее выбытие/инциденты"
                    if blocked_fifo else
                    "Открыть Остатки → Сверка FIFO; сначала обработать продажи, затем подтвердить локальную сверку"
                ),
            })
    except Exception:
        pass
    action_df=pd.DataFrame(action_rows)
    if not action_df.empty:
        action_df["_order"]=action_df["Приоритет"].map({"Критично":0,"Высокий":1,"Средний":2}).fillna(9); action_df=action_df.sort_values(["_order","Тип","Артикул продавца"]).drop(columns="_order")
    alert_count=len(action_df)
    kpis=st.columns(6)
    with kpis[0]: kpi_card("Заказы сегодня",num(data.kpi["orders"]),money(data.kpi["order_amount"]))
    with kpis[1]: kpi_card("Выкупы сегодня",num(data.kpi["sales"]),money(data.kpi["revenue"]))
    with kpis[2]: kpi_card("Реклама сегодня",money(data.kpi["ad_spend"]),f"ДРР {pct(data.kpi['drr'])}")
    shift_note=f"Смена {displayed_shift_date:%d.%m.%Y}" if displayed_shift_date is not None and pd.notna(displayed_shift_date) else "Смена не сформирована"
    if planned_shift > 0:
        if not material_unknown.empty:
            shift_note += " · сырьё не подтверждено"
        else:
            shift_note += f" · сырьём {num(shift_material_covered_units)}/{num(planned_shift)}"
    with kpis[3]: kpi_card("Смена",f"{actual_shift}/{planned_shift}",shift_note)
    with kpis[4]: kpi_card("Готово / в пути",f"{num(ready_total)} / {num(inbound_total)}","Комплектов")
    with kpis[5]: kpi_card("Требует внимания",num(alert_count),f"WB: {sync_age_text} · копия: {backup_age_text}")
    if sync_stale: st.error("Данные WB давно не обновлялись. Выполните синхронизацию.")
    if alert_count == 0:
        st.success("Критичных действий на сегодня нет.")
    else:
        st.warning(f"Найдено {alert_count} сигналов. Начните со вкладки «Сделать сейчас».")

    today_tabs=st.tabs(["Сделать сейчас","Смена","Отгрузки","Сырьё","Закупить","Остатки","Просрочено"])
    with today_tabs[0]:
        if action_df.empty:
            st.success("Оперативных действий нет.")
        else:
            st.dataframe(
                action_df,
                hide_index=True,
                use_container_width=True,
                height=min(520, 92 + 36 * len(action_df)),
            )
    with today_tabs[1]:
        if shift_tasks.empty: st.info("Ближайшее сменное задание не сформировано.")
        else:
            st.caption(f"Показывается смена {displayed_shift_date:%d.%m.%Y}. Сырьём обеспечено {num(shift_material_covered_units)} из {num(planned_shift)} комплектов.")
            cols=[c for c in ["supplier_article","product_name","planned_units","actual_units","status","note"] if c in shift_tasks.columns]
            st.dataframe(shift_tasks[cols].rename(columns={"supplier_article":"Артикул продавца","product_name":"Товар","planned_units":"План, компл.","actual_units":"Факт, компл.","status":"Статус","note":"Примечание"}),hide_index=True,use_container_width=True)
        if not blocked_products_today.empty:
            st.markdown("#### Не вошли в план из-за дефицита сырья")
            blocked_view = blocked_products_today[[
                "Артикул продавца", "Товар", "Материал / цвет", "Нужно произвести, компл.",
                "Дефицит материала, м", "Рулонов докупить"
            ]].copy()
            st.dataframe(
                blocked_view, hide_index=True, use_container_width=True,
                column_config={
                    "Дефицит материала, м": st.column_config.NumberColumn(format="%.1f"),
                    "Рулонов докупить": st.column_config.NumberColumn(format="%d"),
                },
            )
    with today_tabs[2]:
        if unposted_dispatch.empty and pending_receipt.empty: st.success("Непроведённых отгрузок и приёмок нет.")
        else:
            if not unposted_dispatch.empty: st.error(f"Непроведённых отгрузок: {len(unposted_dispatch)}"); st.dataframe(unposted_dispatch,hide_index=True,use_container_width=True)
            if not pending_receipt.empty: st.warning(f"Незакрытых приёмок: {len(pending_receipt)}"); st.dataframe(pending_receipt,hide_index=True,use_container_width=True)
    with today_tabs[3]:
        if shift_material.empty: st.info("Нет сменного задания или норм сырья для ближайшей смены.")
        else:
            view=shift_material[["material_name","Потребность, м","Остаток указан","На складе, м","После смены, м","Статус"]].rename(columns={"material_name":"Материал / цвет"})
            st.dataframe(view,hide_index=True,use_container_width=True,column_config={"Потребность, м":st.column_config.NumberColumn(format="%.1f"),"На складе, м":st.column_config.NumberColumn(format="%.1f"),"После смены, м":st.column_config.NumberColumn(format="%.1f")})
            if not material_shortages.empty: st.error("Сырья на ближайшую смену не хватает. Календарь ограничит выполнимый объём.")
            elif not material_unknown.empty: st.warning("Для части материалов остаток не подтверждён.")
            else: st.success("Сырья на ближайшую смену достаточно.")
    with today_tabs[4]:
        st.markdown("#### Сырьё")
        if material_buy_today.empty:
            st.success("По текущему производственному плану закупка сырья не требуется.")
        else:
            material_buy_view = material_buy_today[[
                "Материал / цвет", "Нужно материала, м", "На складе, м", "Не хватает, м",
                "Рулонов докупить", "Артикул продавца"
            ]].copy()
            st.dataframe(
                material_buy_view, hide_index=True, use_container_width=True,
                column_config={
                    "Нужно материала, м": st.column_config.NumberColumn(format="%.1f"),
                    "На складе, м": st.column_config.NumberColumn(format="%.1f"),
                    "Не хватает, м": st.column_config.NumberColumn(format="%.1f"),
                    "Рулонов докупить": st.column_config.NumberColumn(format="%d"),
                },
            )
        st.markdown("#### Закупаемые товары")
        if purchase_products_today.empty:
            st.success("Закупаемых товаров с критическим остатком нет.")
        else:
            purchase_view = purchase_products_today[[
                "Артикул продавца", "Товар", "Остаток WB", "Продаж/день", "Запас WB, дней",
                "В пути", "Ориентировочно заказать, шт.", "Действие"
            ]].copy()
            st.dataframe(
                purchase_view, hide_index=True, use_container_width=True,
                column_config={
                    "Продаж/день": st.column_config.NumberColumn(format="%.2f"),
                    "Запас WB, дней": st.column_config.NumberColumn(format="%.1f"),
                    "Ориентировочно заказать, шт.": st.column_config.NumberColumn(format="%d"),
                },
            )
        st.markdown("#### Активные заявки")
        active_proc_today = procurement_orders_today[~procurement_orders_today["status"].isin(["Получено", "Отменено"])].copy() if not procurement_orders_today.empty else pd.DataFrame()
        if active_proc_today.empty:
            st.info("Активных заявок пока нет. Откройте отдельный раздел «Закупки».")
        else:
            active_proc_today["Ожидается"] = pd.to_datetime(active_proc_today["expected_date"], errors="coerce")
            active_proc_today["Сумма, ₽"] = pd.to_numeric(active_proc_today["total_amount"], errors="coerce").fillna(0)
            active_proc_today["К оплате, ₽"] = pd.to_numeric(active_proc_today.get("outstanding_amount", 0), errors="coerce").fillna(0)
            st.dataframe(
                active_proc_today[["order_number", "procurement_type", "supplier_name", "status", "Ожидается", "Сумма, ₽", "К оплате, ₽"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "order_number":"№ заявки", "procurement_type":"Тип", "supplier_name":"Поставщик",
                    "status":"Статус", "Ожидается":st.column_config.DateColumn(format="DD.MM.YYYY"),
                    "Сумма, ₽":st.column_config.NumberColumn(format="%.2f"),
                },
            )
    with today_tabs[5]:
        if critical_stock.empty: st.success(f"Товаров с запасом менее {emergency_days_today} дней нет.")
        else:
            stock_view = critical_stock.copy()
            if "Артикул WB" in stock_view.columns:
                stock_view["Источник"] = stock_view["Артикул WB"].apply(lambda value: "Собственное производство" if int(float(value or 0)) in own_nm_today else "Закупаемый товар")
            cols=[c for c in ["Источник","Артикул продавца","Товар","Остаток","Продаж/день","Запас, дней","ДРР, %"] if c in stock_view.columns]
            st.dataframe(stock_view[cols],hide_index=True,use_container_width=True)
    with today_tabs[6]:
        if overdue.empty: st.success("Просроченных заданий нет.")
        else: st.dataframe(overdue,hide_index=True,use_container_width=True)

