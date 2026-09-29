"""Read-only raw-material demand planning and transactional internal PO creation."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
import math
import sqlite3
from typing import Any, Iterable

OPEN_STATUSES = {"Запланировано", "Заказано", "Частично оплачено", "Оплачено", "В пути", "Частично получено"}
CLOSED_STATUSES = {"Отменено", "Получено"}
TASK_CLOSED_STATUSES = {"отменено", "отменена", "закрыто", "закрыта", "закрыт", "завершено", "завершена", "выполнено", "изготовлено", "упаковано", "передано на отгрузку", "отгружено", "принято wb"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(snapshot: dict[str, Any], allocations: list[dict[str, Any]], lines: list[dict[str, Any]], blockers: list[dict[str, Any]]) -> str:
    body = {"snapshot": snapshot, "allocations": allocations, "lines": lines, "blockers": blockers}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _bom_for(boms: list[dict[str, Any]], nm_id: int, chrt_id: int) -> tuple[dict[str, Any] | None, bool]:
    exact = [b for b in boms if int(b.get("nm_id") or 0) == nm_id and int(b.get("chrt_id") or 0) == chrt_id]
    if exact:
        return max(exact, key=lambda b: (int(b.get("version") or 0), int(b.get("id") or 0))), False
    if chrt_id:
        base = [b for b in boms if int(b.get("nm_id") or 0) == nm_id and int(b.get("chrt_id") or 0) == 0]
        if base:
            return max(base, key=lambda b: (int(b.get("version") or 0), int(b.get("id") or 0))), True
    return None, False


def _bom_signature(bom: dict[str, Any] | None) -> tuple[tuple[str, str, float], ...] | None:
    if not bom:
        return None
    try:
        return tuple(sorted((str(c["material_key"]), str(c["unit"]).strip().casefold(), round(float(c["quantity_per_unit"]), 9)) for c in bom.get("materials", [])))
    except (KeyError, TypeError, ValueError):
        return None


def build_supply_preview(snapshot: dict[str, Any], variant_allocations: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Pure deterministic calculation. Inputs are a serializable tenant snapshot."""
    allocations = [dict(a) for a in variant_allocations]
    tasks = [dict(t) for t in snapshot.get("tasks", [])]
    variants = [dict(v) for v in snapshot.get("variants", [])]
    boms = [dict(b) for b in snapshot.get("boms", [])]
    inventory = {str(i.get("material_key") or ""): dict(i) for i in snapshot.get("inventory", [])}
    fifo_inventory = {str(i.get("material_key") or ""): float(i.get("remaining_qty") or 0) for i in snapshot.get("fifo_inventory", [])}
    orders = [dict(o) for o in snapshot.get("open_orders", [])]
    suppliers = [dict(s) for s in snapshot.get("suppliers", []) if int(s.get("active", 1) or 0)]
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    demands: list[dict[str, Any]] = []
    variant_demands: list[dict[str, Any]] = []
    variant_map: dict[int, list[int]] = {}
    for v in variants:
        variant_map.setdefault(int(v.get("nm_id") or 0), []).append(int(v.get("chrt_id") or 0))
    allocation_by_task: dict[str, list[dict[str, Any]]] = {}
    seen_allocations: set[tuple[str, int]] = set()
    for item in allocations:
        identity = (str(item.get("task_key") or ""), int(item.get("chrt_id") or 0))
        if identity in seen_allocations:
            blockers.append({"code": "duplicate_variant_allocation", "task_key": identity[0], "message": "Вариант задания указан в распределении повторно."})
        seen_allocations.add(identity)
        allocation_by_task.setdefault(str(item.get("task_key") or ""), []).append(item)
    task_keys = {str(task.get("task_key") or "") for task in tasks}
    if any(key not in task_keys for key in allocation_by_task):
        blockers.append({"code": "unknown_allocation_task", "message": "Распределение относится к заданию вне выбранного плана."})
    for task in tasks:
        key = str(task.get("task_key") or "")
        status = str(task.get("status") or "").strip()
        units = max(0, int(task.get("planned_units") or 0) - int(task.get("actual_units") or 0))
        nm_id, chrt_id = int(task.get("nm_id") or 0), int(task.get("chrt_id") or 0)
        task_date = str(task.get("task_date") or "")
        if not key or not (int(snapshot.get("start_date", "0000-00-00") <= task_date <= snapshot.get("end_date", "9999-99-99"))):
            continue
        if status.casefold() in TASK_CLOSED_STATUSES or units <= 0:
            continue
        base_ref = {"task_key": key, "nm_id": nm_id, "chrt_id": chrt_id, "units": units, "task_date": task_date}
        if nm_id <= 0 or not task_date:
            blockers.append({"code": "invalid_demand", "task_key": key, "message": "Заданию не хватает артикула или даты."})
            continue
        if chrt_id == 0 and len(set(variant_map.get(nm_id, []))) > 1:
            variant_ids = sorted(set(variant_map[nm_id]))
            signatures = [_bom_signature(_bom_for(boms, nm_id, variant)[0]) for variant in variant_ids]
            if all(signature is not None and signature == signatures[0] for signature in signatures):
                demands.append({**base_ref, "variant_aggregate": variant_ids})
                warnings.append({"code": "variant_demand_aggregated", "task_key": key, "chrt_ids": variant_ids})
                continue
            split = allocation_by_task.get(key, [])
            allocated_total = 0
            valid = True
            for entry in split:
                variant = int(entry.get("chrt_id") or 0)
                qty = entry.get("units")
                if variant not in variant_ids or isinstance(qty, bool) or not isinstance(qty, (int, float)) or not math.isfinite(qty) or qty <= 0 or int(qty) != qty:
                    valid = False
                    continue
                allocated_total += qty
                demands.append({**base_ref, "chrt_id": variant, "units": qty, "variant_allocation": True})
            if not split:
                variant_demands.append({"task_key": key, "nm_id": nm_id, "units": units, "chrt_ids": variant_ids, "task_date": task_date})
                blockers.append({"code": "ambiguous_variant_demand", "task_key": key, "nm_id": nm_id, "units": units, "chrt_ids": variant_ids, "message": "Для плана без размера состав вариантов различается; распределите выпуск по вариантам."})
                continue
            if not valid or allocated_total != units:
                blockers.append({"code": "incomplete_variant_allocation", "task_key": key, "expected_units": units, "allocated_units": allocated_total, "message": "Распределите весь объём задания между допустимыми вариантами."})
                demands = [d for d in demands if not (d["task_key"] == key and d.get("variant_allocation"))]
                continue
            continue
        demands.append(base_ref)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for demand in demands:
        nm_id, chrt_id = int(demand["nm_id"]), int(demand["chrt_id"])
        variant_ids = demand.get("variant_aggregate") or [chrt_id]
        selected: list[tuple[int, dict[str, Any], bool]] = []
        for variant in variant_ids:
            bom, fallback = _bom_for(boms, nm_id, variant)
            if bom is None:
                blockers.append({"code": "missing_bom", "task_key": demand["task_key"], "nm_id": nm_id, "chrt_id": variant, "message": "Для спроса не найдена спецификация."})
                selected = []
                break
            selected.append((variant, bom, fallback))
        if not selected:
            continue
        # Identical multi-variant recipe is represented once using one variant's version.
        if demand.get("variant_aggregate"):
            selected = selected[:1]
        for variant, bom, fallback in selected:
            if not bom.get("materials"):
                blockers.append({"code": "empty_bom", "task_key": demand["task_key"], "bom_version_id": bom.get("id"), "message": "В спецификации нет сырья."})
                continue
            for component in bom["materials"]:
                try:
                    material_key = str(component.get("material_key") or "").strip()
                    name, unit = str(component.get("material_name") or "").strip(), str(component.get("unit") or "").strip()
                    rate = float(component.get("quantity_per_unit"))
                    required = float(demand["units"]) * rate
                except (TypeError, ValueError):
                    required, material_key, name, unit = 0, "", "", ""
                if not material_key or not name or not unit or not math.isfinite(required) or required <= 0:
                    blockers.append({"code": "invalid_bom_component", "task_key": demand["task_key"], "bom_version_id": bom.get("id"), "message": "Компонент BOM имеет неверное имя, единицу или расход."})
                    continue
                group_key = (material_key, unit.casefold())
                row = grouped.setdefault(group_key, {"material_key": material_key, "material_name": name, "unit": unit, "required_base_qty": 0.0, "demand_by_date": {}, "source_refs": [], "bom_version_ids": [], "fallback_to_base_bom": False, "due_date": demand["task_date"]})
                row["required_base_qty"] += required
                row["demand_by_date"][demand["task_date"]] = row["demand_by_date"].get(demand["task_date"], 0.0) + required
                row["due_date"] = min(row["due_date"], demand["task_date"])
                row["source_refs"].append({"task_key": demand["task_key"], "nm_id": nm_id, "chrt_id": variant, "units": demand["units"], "bom_version": bom.get("version"), "bom_version_id": bom.get("id")})
                row["bom_version_ids"].append(int(bom.get("id") or 0))
                row["fallback_to_base_bom"] = bool(row["fallback_to_base_bom"] or fallback)
    lines: list[dict[str, Any]] = []
    for row in grouped.values():
        key, unit = row["material_key"], row["unit"]
        stock = inventory.get(key)
        stock_unit = str(stock.get("unit") or "") if stock else ""
        available: float | None = None
        pack_size: float | None = None
        tracking = str(stock.get("tracking_mode") or "quantity") if stock else "quantity"
        if stock and stock_unit.casefold() == unit.casefold() and int(stock.get("balance_known") or 0) == 1:
            try:
                available = max(0, float(stock.get("partial_meters") or 0) + (float(stock.get("full_rolls") or 0) * float(stock.get("roll_length") or 0) if tracking == "packaged" else 0))
            except (TypeError, ValueError):
                available = None
            if available is not None and not math.isfinite(available):
                available = None
                blockers.append({"code": "invalid_inventory_balance", "material_key": key, "message": "Остаток материала не является конечным числом; сверьте склад."})
            fifo_qty = fifo_inventory.get(key, 0.0)
            if available is not None and abs(available - fifo_qty) > 0.001:
                blockers.append({"code": "fifo_stock_mismatch", "material_key": key, "physical_qty": round(available, 6), "fifo_qty": round(fifo_qty, 6), "message": "Физический остаток расходится с активными FIFO-слоями; сначала сверьте склад."})
                available = 0.0
        elif not stock or stock_unit.casefold() != unit.casefold() or int(stock.get("balance_known") or 0) != 1:
            blockers.append({"code": "unknown_inventory_balance" if stock and int(stock.get("balance_known") or 0) != 1 else "unit_or_inventory_mismatch", "material_key": key, "message": "Подтвердите остаток сырья и совместимость единиц."})
        if stock and stock_unit.casefold() != unit.casefold():
            available = 0.0
        if stock and tracking == "packaged":
            try:
                pack_size = float(stock.get("roll_length") or 0)
            except (TypeError, ValueError):
                pack_size = 0
            if not pack_size or not math.isfinite(pack_size):
                blockers.append({"code": "invalid_pack_size", "material_key": key, "message": "Не задан корректный размер упаковки."})
                pack_size = None
        po_covered = 0.0
        arrivals: list[tuple[str, float]] = []
        for order in orders:
            if order.get("material_key") != key:
                continue
            status = str(order.get("status") or "")
            if status == "Отменено":
                continue
            try:
                quantity = float(order.get("quantity") or 0)
                posted = float(order.get("posted_quantity") or 0)
                if not math.isfinite(quantity) or not math.isfinite(posted) or posted < 0 or quantity < posted:
                    raise ValueError("invalid quantities")
                remaining = quantity - posted
            except (ValueError, TypeError):
                blockers.append({"code": "invalid_open_order_quantity", "material_key": key, "message": "Количество или проведённый остаток закупки некорректны; сверьте заявку."})
                continue
            eta = str(order.get("expected_date") or "")
            order_unit = str(order.get("unit") or "").strip().casefold()
            if order_unit == "рулон" and tracking == "packaged":
                try:
                    roll_length = float(order.get("roll_length") or 0)
                except (TypeError, ValueError):
                    roll_length = 0
                if not math.isfinite(roll_length) or roll_length <= 0:
                    blockers.append({"code": "invalid_open_order_pack", "material_key": key, "order_id": order.get("order_id"), "message": "В открытой заявке не задан корректный метраж рулона."})
                    continue
                remaining *= roll_length
            elif order_unit != unit.casefold():
                blockers.append({"code": "open_order_unit_mismatch", "material_key": key, "order_id": order.get("order_id"), "order_unit": order.get("unit"), "material_unit": unit, "message": "Единица открытой закупки не совместима с единицей материала."})
                continue
            if status in CLOSED_STATUSES and remaining > 0:
                blockers.append({"code": "closed_order_residual", "material_key": key, "order_id": order.get("order_id"), "message": "У закрытой заявки остался неполученный остаток; сверьте её."})
            if status in OPEN_STATUSES and eta and eta <= max(row["demand_by_date"]):
                arrivals.append((eta, remaining))
                po_covered += remaining
            elif status in OPEN_STATUSES:
                warnings.append({"code": "open_order_eta_risk", "material_key": key, "order_id": order.get("order_id"), "expected_date": eta or None, "due_date": row["due_date"]})
        row["physical_available_base_qty"] = round(available, 6) if available is not None else None
        cumulative_demand = 0.0
        shortage = 0.0
        for need_date, need_qty in sorted(row["demand_by_date"].items()):
            cumulative_demand += need_qty
            arrived = sum(qty for eta, qty in arrivals if eta <= need_date)
            shortage = max(shortage, cumulative_demand - (available or 0.0) - arrived)
        row["open_order_covered_base_qty"] = round(max(0.0, min(po_covered, row["required_base_qty"] - (available or 0.0) - shortage)), 6)
        row["shortage_base_qty"] = round(max(0.0, shortage), 6)
        row["pack_size_base_qty"] = pack_size
        row["tracking_mode"] = tracking
        row["suggested_purchase_qty"] = round(math.ceil((row["shortage_base_qty"] - 1e-9) / pack_size), 6) if pack_size and row["shortage_base_qty"] > 0 else row["shortage_base_qty"]
        row["supplier_candidates"] = [{"id": int(s["id"]), "name": s["name"], "currency": s.get("default_currency", "RUB"), "lead_time_days": int(s.get("lead_time_days") or 0)} for s in suppliers]
        if row["fallback_to_base_bom"]:
            warnings.append({"code": "fallback_to_base_bom", "material_key": key})
        if stock and stock_unit.casefold() != unit.casefold():
            blockers.append({"code": "unit_mismatch", "material_key": key, "bom_unit": unit, "stock_unit": stock_unit})
        row["required_base_qty"] = round(row["required_base_qty"], 6)
        lines.append(row)
    material_units: dict[str, set[str]] = {}
    for line in lines:
        material_units.setdefault(line["material_key"], set()).add(line["unit"].casefold())
    for key, units in material_units.items():
        if len(units) > 1:
            blockers.append({"code": "conflicting_bom_units", "material_key": key, "message": "В спецификациях для одного материала указаны разные единицы. Исправьте их перед закупкой."})
    lines.sort(key=lambda line: (line["due_date"], line["material_key"], line["unit"].casefold()))
    result = {"start_date": snapshot.get("start_date"), "end_date": snapshot.get("end_date"), "lines": lines, "blockers": blockers, "warnings": warnings, "variant_demands": variant_demands, "preview_fingerprint": ""}
    result["preview_fingerprint"] = _fingerprint(snapshot, allocations, lines, blockers)
    return result


def load_supply_plan_snapshot(conn: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, Any]:
    """Load all planner inputs through the caller's tenant connection/read transaction."""
    start_date, end_date = str(start_date), str(end_date)
    date.fromisoformat(start_date); date.fromisoformat(end_date)
    if start_date > end_date:
        raise ValueError("Начало периода позже окончания.")
    tasks = [dict(row) for row in conn.execute("""SELECT * FROM execution_tasks
        WHERE (LOWER(TRIM(task_type))='production' OR TRIM(task_type) IN ('Производство','производство'))
          AND task_date BETWEEN ? AND ? AND COALESCE(planned_units,0)>COALESCE(actual_units,0)
        ORDER BY task_date,task_key""", (start_date, end_date)).fetchall()]
    variants: list[dict[str, Any]] = []
    for row in conn.execute("SELECT nm_id,raw_json FROM products_catalog").fetchall():
        try:
            raw = json.loads(row["raw_json"] or "{}")
        except (TypeError, ValueError):
            raw = {}
        for size in raw.get("sizes", []) if isinstance(raw, dict) else []:
            if isinstance(size, dict):
                try:
                    variant_id = int(size.get("chrtID", size.get("chrtId", size.get("chrt_id", 0))) or 0)
                except (ValueError, TypeError):
                    continue
                if variant_id > 0:
                    variants.append({"nm_id": int(row["nm_id"]), "chrt_id": variant_id})
    boms = []
    for row in conn.execute("SELECT id,nm_id,chrt_id,version,materials_json FROM manufacturing_boms ORDER BY nm_id,chrt_id,version DESC").fetchall():
        try:
            materials = json.loads(row["materials_json"] or "[]")
        except (TypeError, ValueError):
            materials = []
        boms.append({"id": row["id"], "nm_id": row["nm_id"], "chrt_id": row["chrt_id"], "version": row["version"], "materials": materials})
    inventory = [dict(r) for r in conn.execute("SELECT material_key,material_name,balance_known,full_rolls,partial_meters,roll_length,unit,tracking_mode FROM material_inventory_color").fetchall()]
    po_rows = conn.execute("""SELECT o.id AS order_id,o.procurement_type,o.status,o.expected_date,i.item_type,
        i.material_key,i.material_name,i.quantity,i.posted_quantity,i.unit,i.roll_length
        FROM procurement_orders o JOIN procurement_items i ON i.order_id=o.id
        WHERE o.procurement_type='Сырьё' AND i.item_type='Сырьё'""").fetchall()
    suppliers = [dict(r) for r in conn.execute("SELECT id,name,default_currency,lead_time_days,active FROM suppliers WHERE active=1 ORDER BY name").fetchall()]
    fifo_inventory = [dict(r) for r in conn.execute("SELECT material_key,COALESCE(SUM(remaining_meters),0) AS remaining_qty FROM material_cost_layers WHERE status='active' GROUP BY material_key").fetchall()]
    # A hashable source revision lets confirm detect writes to otherwise unchanged selected fields too.
    revisions = {
        "tasks": [tuple(r) for r in conn.execute("SELECT task_key,task_date,nm_id,chrt_id,planned_units,actual_units,status,updated_at FROM execution_tasks WHERE task_date BETWEEN ? AND ? ORDER BY task_key", (start_date, end_date)).fetchall()],
        "boms": [tuple(r) for r in conn.execute("SELECT id,nm_id,chrt_id,version,materials_json FROM manufacturing_boms ORDER BY id").fetchall()],
        "inventory": [tuple(r) for r in conn.execute("SELECT material_key,balance_known,full_rolls,partial_meters,roll_length,unit,tracking_mode,updated_at FROM material_inventory_color ORDER BY material_key").fetchall()],
        "orders": [tuple(r) for r in po_rows],
        "fifo": [tuple(r) for r in fifo_inventory],
    }
    return {"start_date": start_date, "end_date": end_date, "tasks": tasks, "variants": variants, "boms": boms, "inventory": inventory, "fifo_inventory": fifo_inventory, "open_orders": [dict(r) for r in po_rows], "suppliers": suppliers, "revisions": revisions}


def _ensure_confirmation_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS supply_plan_confirmations (
        confirmation_key TEXT PRIMARY KEY,
        request_hash TEXT NOT NULL,
        preview_fingerprint TEXT NOT NULL,
        order_ids_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")


def confirm_supply_plan(conn: sqlite3.Connection, confirmation_key: str, request_hash: str,
                        preview_fingerprint: str, current_preview: dict[str, Any],
                        edited_lines: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Create internal planned procurement orders and idempotency record atomically.

    The caller must freshly rebuild ``current_preview`` from this same tenant DB
    connection immediately before invoking this function.
    """
    from .procurement import _next_procurement_number
    edits = [dict(row) for row in edited_lines]
    _ensure_confirmation_schema(conn)
    owns_transaction = not conn.in_transaction
    savepoint = "supply_plan_confirm"
    conn.execute("BEGIN IMMEDIATE" if owns_transaction else f"SAVEPOINT {savepoint}")
    try:
        prior = conn.execute("SELECT request_hash,order_ids_json FROM supply_plan_confirmations WHERE confirmation_key=?", (confirmation_key,)).fetchone()
        if prior:
            if prior["request_hash"] != request_hash:
                raise ValueError("idempotency_conflict")
            ids = json.loads(prior["order_ids_json"])
            orders = [dict(row) for row in conn.execute(f"SELECT id,order_number FROM procurement_orders WHERE id IN ({','.join('?' for _ in ids)})", ids).fetchall()] if ids else []
            if not owns_transaction:
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            return {"confirmation_key": confirmation_key, "order_ids": ids, "orders": orders, "replayed": True}
        if preview_fingerprint != current_preview.get("preview_fingerprint"):
            raise ValueError("stale_preview")
        if current_preview.get("blockers"):
            raise ValueError("preview_blocked")
        eligible = {(str(row["material_key"]), str(row["unit"]).casefold()): row for row in current_preview.get("lines", [])}
        if not edits:
            raise ValueError("Добавьте количество для заказа хотя бы по одной строке.")
        groups: dict[tuple[int | None, str, str], list[dict[str, Any]]] = {}
        seen_lines: set[tuple[str, str]] = set()
        for edit in edits:
            key, unit = str(edit.get("material_key") or ""), str(edit.get("unit") or "")
            identity = (key, unit.casefold())
            if identity in seen_lines:
                raise ValueError("Материал указан в заявке повторно.")
            seen_lines.add(identity)
            line = eligible.get((key, unit.casefold()))
            if line is None:
                raise ValueError("Строка не принадлежит актуальному предпросмотру.")
            try:
                qty, min_qty = float(edit.get("quantity")), float(line["suggested_purchase_qty"])
                supplier_id = int(edit["supplier_id"]) if edit.get("supplier_id") is not None else None
            except (TypeError, ValueError, KeyError):
                raise ValueError("Проверьте количество и поставщика в строке заказа.")
            if not math.isfinite(qty) or qty <= 0 or qty + 1e-8 < min_qty:
                raise ValueError("Количество заказа меньше рассчитанной потребности или неверно.")
            if supplier_id is None:
                raise ValueError("Для каждой позиции выберите активного поставщика.")
            if line.get("tracking_mode") == "packaged":
                if not line.get("pack_size_base_qty") or abs(qty-round(qty)) > 1e-8:
                    raise ValueError("Количество рулонов должно быть целым.")
            if supplier_id is not None and not conn.execute("SELECT 1 FROM suppliers WHERE id=? AND active=1", (supplier_id,)).fetchone():
                raise ValueError("Выбранный поставщик не найден или отключён.")
            eta = str(edit.get("expected_date") or "")
            try:
                date.fromisoformat(eta)
            except ValueError:
                raise ValueError("Укажите ожидаемую дату поставки.")
            if qty == 0:
                continue
            supplier_currency = "RUB"
            if supplier_id is not None:
                supplier_currency = str(conn.execute("SELECT default_currency FROM suppliers WHERE id=?", (supplier_id,)).fetchone()["default_currency"] or "RUB").upper()
            group_key = (supplier_id, eta, supplier_currency)
            groups.setdefault(group_key, []).append({**line, "order_quantity": qty, "order_unit": "рулон" if line.get("tracking_mode") == "packaged" else line["unit"], "supplier_id": supplier_id})
        now = datetime.now().isoformat(timespec="seconds")
        order_ids: list[int] = []
        orders_out: list[dict[str, Any]] = []
        suppliers_by_id = {int(row["id"]): str(row["name"]) for row in conn.execute("SELECT id,name FROM suppliers WHERE active=1").fetchall()}
        for index, ((supplier_id, eta, currency), rows) in enumerate(sorted(groups.items(), key=lambda item: (item[0][0] or 0, item[0][1], item[0][2]))):
            if not rows:
                continue
            supplier_name = suppliers_by_id.get(supplier_id, "") if supplier_id else ""
            order_number = _next_procurement_number(conn)
            source_key = f"supply-plan:{confirmation_key}:{index}"
            cur = conn.execute("""INSERT INTO procurement_orders(
                order_number,procurement_type,supplier_name,supplier_id,status,order_date,expected_date,currency,exchange_rate,note,source_key,created_at,updated_at
            ) VALUES (?, 'Сырьё', ?, ?, 'Запланировано', ?, ?, ?, 1, ?, ?, ?, ?)""",
                (order_number, supplier_name, supplier_id, date.today().isoformat(), eta, currency,
                 "Внутренняя заявка из планирования потребности; поставщику не отправлена.", source_key, now, now))
            order_id = int(cur.lastrowid)
            order_ids.append(order_id)
            orders_out.append({"id": order_id, "order_number": order_number})
            for line in rows:
                roll = float(line.get("pack_size_base_qty") or 0) if line["order_unit"] == "рулон" else 0.0
                conn.execute("""INSERT INTO procurement_items(
                    order_id,item_type,material_key,material_name,quantity,unit,roll_length,supplier_unit_price,exchange_rate,delivery_unit_foreign,extra_unit_rub,unit_price,received_quantity,posted_quantity,note,created_at,updated_at
                ) VALUES (?, 'Сырьё', ?, ?, ?, ?, ?, 0, 1, 0, 0, 0, 0, 0, ?, ?, ?)""",
                    (order_id, line["material_key"], line["material_name"], line["order_quantity"], line["order_unit"], roll,
                     "Потребность рассчитана по BOM; цена не указана.", now, now))
        if not order_ids:
            raise ValueError("Не осталось позиций с количеством для создания заявки.")
        conn.execute("INSERT INTO supply_plan_confirmations(confirmation_key,request_hash,preview_fingerprint,order_ids_json,created_at) VALUES (?,?,?,?,?)",
                     (confirmation_key, request_hash, preview_fingerprint, _canonical(order_ids), now))
        if not owns_transaction:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        return {"confirmation_key": confirmation_key, "order_ids": order_ids, "orders": orders_out, "replayed": False}
    except Exception:
        if owns_transaction:
            conn.rollback()
        else:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
