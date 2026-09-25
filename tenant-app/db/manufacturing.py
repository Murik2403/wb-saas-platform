"""Versioned manufacturing: actual FIFO materials, piecework and equipment wear.

All posting uses one SQLite transaction; snapshots survive later master-data
changes. New BOM workflows require explicit, sufficient FIFO layers (no inferred
opening price). Legacy one-material production remains in production.py.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import math
import sqlite3
from typing import Any

from .core import connect


def init_manufacturing_schema(conn: sqlite3.Connection) -> None:
    # execute individually: executescript would commit the caller's migration.
    statements = [
        """CREATE TABLE IF NOT EXISTS manufacturing_boms (
            id INTEGER PRIMARY KEY, nm_id INTEGER NOT NULL, chrt_id INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL,
            materials_json TEXT NOT NULL, operations_json TEXT NOT NULL,
            packaging_per_unit REAL NOT NULL, other_per_unit REAL NOT NULL,
            note TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(nm_id,chrt_id,version))""",
        """CREATE TABLE IF NOT EXISTS manufacturing_equipment (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, acquisition_cost_rub REAL NOT NULL,
            salvage_value_rub REAL NOT NULL, resource_quantity REAL NOT NULL,
            resource_unit TEXT NOT NULL, consumed_resource REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS manufacturing_piecework (
            id INTEGER PRIMARY KEY, source_key TEXT NOT NULL, nm_id INTEGER NOT NULL, chrt_id INTEGER NOT NULL DEFAULT 0,
            operation_name TEXT NOT NULL, worker TEXT NOT NULL, quantity REAL NOT NULL,
            rate_per_unit REAL NOT NULL, work_date TEXT NOT NULL, event_key TEXT NOT NULL UNIQUE,
            request_hash TEXT NOT NULL, release_movement_id INTEGER, created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS manufacturing_releases (
            id INTEGER PRIMARY KEY, movement_id INTEGER NOT NULL UNIQUE,
            source_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL, nm_id INTEGER NOT NULL,
            chrt_id INTEGER NOT NULL DEFAULT 0,
            quantity INTEGER NOT NULL, production_date TEXT NOT NULL, bom_version_id INTEGER NOT NULL,
            materials_json TEXT NOT NULL, labor_json TEXT NOT NULL, equipment_json TEXT NOT NULL,
            material_cost_rub REAL NOT NULL, labor_cost_rub REAL NOT NULL,
            depreciation_cost_rub REAL NOT NULL, packaging_cost_rub REAL NOT NULL,
            other_cost_rub REAL NOT NULL, total_cost_rub REAL NOT NULL, unit_cost_rub REAL NOT NULL,
            finished_layer_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'applied',
            created_at TEXT NOT NULL, reversed_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS manufacturing_defects (
            id INTEGER PRIMARY KEY, movement_id INTEGER NOT NULL UNIQUE,
            layer_id INTEGER NOT NULL, material_key TEXT NOT NULL, material_name TEXT NOT NULL,
            quantity REAL NOT NULL, unit TEXT NOT NULL, cost_rub REAL NOT NULL,
            reason TEXT NOT NULL, movement_date TEXT NOT NULL, event_key TEXT NOT NULL UNIQUE,
            request_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'applied',
            created_at TEXT NOT NULL, reversed_at TEXT)""",
        "CREATE INDEX IF NOT EXISTS idx_manufacturing_piecework_job ON manufacturing_piecework(source_key)",
    ]
    for statement in statements:
        conn.execute(statement)

    # Upgrade the NM-only BOM table while preserving old versions as the
    # legacy/default variant (chrt_id=0). Rebuilding is required because its
    # original UNIQUE(nm_id,version) constraint prevents per-variant versions.
    bom_columns = {row["name"] for row in conn.execute("PRAGMA table_info(manufacturing_boms)")}
    if "chrt_id" not in bom_columns:
        conn.execute("ALTER TABLE manufacturing_boms RENAME TO manufacturing_boms_legacy")
        conn.execute("""CREATE TABLE manufacturing_boms (
            id INTEGER PRIMARY KEY, nm_id INTEGER NOT NULL, chrt_id INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL, materials_json TEXT NOT NULL, operations_json TEXT NOT NULL,
            packaging_per_unit REAL NOT NULL, other_per_unit REAL NOT NULL,
            note TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(nm_id,chrt_id,version))""")
        conn.execute("""INSERT INTO manufacturing_boms(
            id,nm_id,chrt_id,version,materials_json,operations_json,packaging_per_unit,
            other_per_unit,note,created_at)
            SELECT id,nm_id,0,version,materials_json,operations_json,packaging_per_unit,
                   other_per_unit,note,created_at FROM manufacturing_boms_legacy""")
        conn.execute("DROP TABLE manufacturing_boms_legacy")
    release_columns = {row["name"] for row in conn.execute("PRAGMA table_info(manufacturing_releases)")}
    if "chrt_id" not in release_columns:
        conn.execute("ALTER TABLE manufacturing_releases ADD COLUMN chrt_id INTEGER NOT NULL DEFAULT 0")
    piecework_columns = {row["name"] for row in conn.execute("PRAGMA table_info(manufacturing_piecework)")}
    if "chrt_id" not in piecework_columns:
        conn.execute("ALTER TABLE manufacturing_piecework ADD COLUMN chrt_id INTEGER NOT NULL DEFAULT 0")
    layer_columns = {row["name"] for row in conn.execute("PRAGMA table_info(finished_goods_cost_layers)")}
    if "producer_release_id" not in layer_columns:
        conn.execute("ALTER TABLE finished_goods_cost_layers ADD COLUMN producer_release_id INTEGER REFERENCES manufacturing_releases(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_manufacturing_releases_finished_layer ON manufacturing_releases(finished_layer_id)")
    # Recover typed provenance for historical manufacturing layers where the
    # release row still exists. Opening/marketplace/other layers intentionally
    # remain NULL: their recipe provenance cannot be inferred safely.
    conn.execute("""
        UPDATE finished_goods_cost_layers
        SET producer_release_id=(SELECT id FROM manufacturing_releases WHERE finished_layer_id=finished_goods_cost_layers.id LIMIT 1)
        WHERE producer_release_id IS NULL AND (
            SELECT COUNT(*) FROM manufacturing_releases WHERE finished_layer_id=finished_goods_cost_layers.id
        ) = 1
    """)


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name}: требуется число")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}: требуется число") from exc
    if not math.isfinite(number) or number < 0 or (positive and number <= 0):
        raise ValueError(f"{name}: требуется конечное {'положительное' if positive else 'неотрицательное'} число")
    if number > 1e12:
        raise ValueError(f"{name}: значение слишком велико")
    return number


def _integer(value: Any, name: str) -> int:
    number = _number(value, name, positive=True)
    if not number.is_integer():
        raise ValueError(f"{name}: требуется целое число")
    return int(number)


def _variant_id(value: Any) -> int:
    """WB chrtID may be zero only for the explicitly legacy nmID-level row."""
    if isinstance(value, bool):
        raise ValueError("Вариант: требуется целое неотрицательное число")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Вариант: требуется целое неотрицательное число") from exc
    if number < 0 or str(value).strip() not in {str(number), f"{number}.0"}:
        raise ValueError("Вариант: требуется целое неотрицательное число")
    return number


def _text(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 500:
        raise ValueError(f"{name}: заполните значение длиной до 500 символов")
    return result


def _day(value: Any) -> str:
    return date.fromisoformat(str(value)).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _decode(row: sqlite3.Row) -> dict:
    result = dict(row)
    for field in ("materials", "operations", "labor", "equipment"):
        key = field + "_json"
        if key in result:
            result[field] = json.loads(result.pop(key))
    result.pop("request_hash", None)
    return result


def _hydrate_semi_finished_provenance(conn: sqlite3.Connection, release: dict) -> dict:
    """Attach typed producer/BOM provenance to exact FIFO layer allocations.

    Layers created before the producer link migration (and opening/external
    layers) remain explicitly unknown; their costs still come from the exact
    consumed layer and are never filtered by BOM version.
    """
    for component in release.get("materials", []):
        if component.get("component_type") != "semi_finished":
            continue
        pinned_version = component.get("component_bom_version_id")
        for allocation in component.get("allocations", []):
            source = conn.execute(
                """SELECT l.producer_release_id,l.source_type,r.bom_version_id,r.source_key
                   FROM finished_goods_cost_layers l
                   LEFT JOIN manufacturing_releases r ON r.id=l.producer_release_id
                   WHERE l.id=?""",
                (int(allocation["layer_id"]),),
            ).fetchone()
            producer_id = int(source["producer_release_id"]) if source and source["producer_release_id"] is not None else None
            source_bom_version = int(source["bom_version_id"]) if source and source["bom_version_id"] is not None else None
            allocation.update(
                source_release_id=producer_id,
                source_release_key=str(source["source_key"]) if source and producer_id is not None else None,
                source_bom_version_id=source_bom_version,
                source_bom_version_mismatch=(source_bom_version != int(pinned_version)) if source_bom_version is not None and pinned_version is not None else None,
                source_provenance=(
                    "manufacturing" if producer_id is not None
                    else "legacy_manufacturing_unknown" if str(source["source_type"] or "").casefold() == "production"
                    else "opening_stock_unknown" if str(source["source_type"] or "").casefold().startswith("opening")
                    else "other_source_unknown"
                ),
            )
    return release


def _equipment(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["rate_per_resource_unit"] = (item["acquisition_cost_rub"] - item["salvage_value_rub"]) / item["resource_quantity"]
    return item


def _bom_for_component(conn: sqlite3.Connection, nm_id: int, chrt_id: int) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM manufacturing_boms WHERE nm_id=? AND chrt_id=? ORDER BY version DESC LIMIT 1",
        (int(nm_id), int(chrt_id)),
    ).fetchone()
    if row is None and chrt_id:
        row = conn.execute(
            "SELECT * FROM manufacturing_boms WHERE nm_id=? AND chrt_id=0 ORDER BY version DESC LIMIT 1",
            (int(nm_id),),
        ).fetchone()
    return row


def _assert_acyclic_bom_route(conn: sqlite3.Connection, component_nm_id: int, component_chrt_id: int,
                               target: tuple[int, int], path: set[tuple[int, int]], depth: int = 1,
                               visited_depths: dict[tuple[int, int], int] | None = None) -> int:
    identity = (int(component_nm_id), int(component_chrt_id))
    if identity == target or identity in path:
        raise ValueError("В маршруте производства обнаружен цикл")
    if depth > 8:
        raise ValueError("Глубина маршрута не должна превышать 8 этапов")
    visited_depths = visited_depths if visited_depths is not None else {}
    if visited_depths.get(identity, 0) >= depth:
        return depth
    visited_depths[identity] = depth
    bom = _bom_for_component(conn, *identity)
    if bom is None:
        raise ValueError(f"Спецификация полуфабриката {identity[0]} / {identity[1]} не найдена")
    next_path = {*path, identity}
    max_depth = depth
    for nested in json.loads(str(bom["materials_json"] or "[]")):
        if str(nested.get("component_type") or "raw_material") != "semi_finished":
            continue
        child_depth = _assert_acyclic_bom_route(
            conn, int(nested["nm_id"]), int(nested["chrt_id"]), target, next_path, depth + 1,
            visited_depths,
        )
        max_depth = max(max_depth, child_depth)
    return max_depth


def save_equipment(name: str, acquisition_cost_rub: float, salvage_value_rub: float,
                   resource_quantity: float, resource_unit: str, equipment_id: int | None = None) -> dict:
    name = _text(name, "Название оборудования")
    cost = _number(acquisition_cost_rub, "Стоимость")
    salvage = _number(salvage_value_rub, "Ликвидационная стоимость")
    resource = _number(resource_quantity, "Ресурс", positive=True)
    if salvage > cost or resource_unit not in {"units", "hours"}:
        raise ValueError("Проверьте ликвидационную стоимость и единицу ресурса")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if equipment_id is None:
            cur = conn.execute("INSERT INTO manufacturing_equipment(name,acquisition_cost_rub,salvage_value_rub,resource_quantity,resource_unit,updated_at) VALUES (?,?,?,?,?,?)", (name,cost,salvage,resource,resource_unit,_now()))
            equipment_id = int(cur.lastrowid)
        else:
            equipment_id = _integer(equipment_id, "Оборудование")
            current = conn.execute("SELECT * FROM manufacturing_equipment WHERE id=?", (equipment_id,)).fetchone()
            if current is None:
                raise ValueError("Оборудование не найдено")
            # Do not change historical depreciation basis once the asset is used.
            used = any(any(item["equipment_id"] == equipment_id for item in json.loads(row[0])) for row in conn.execute("SELECT equipment_json FROM manufacturing_releases"))
            if used and any(current[k] != v for k,v in (("acquisition_cost_rub",cost),("salvage_value_rub",salvage),("resource_quantity",resource),("resource_unit",resource_unit))):
                raise ValueError("Оборудование уже использовано: создайте новую запись для новой базы амортизации")
            conn.execute("UPDATE manufacturing_equipment SET name=?,acquisition_cost_rub=?,salvage_value_rub=?,resource_quantity=?,resource_unit=?,updated_at=? WHERE id=?", (name,cost,salvage,resource,resource_unit,_now(),equipment_id))
        return _equipment(conn.execute("SELECT * FROM manufacturing_equipment WHERE id=?", (equipment_id,)).fetchone())


def list_equipment() -> list[dict]:
    with connect() as conn:
        return [_equipment(r) for r in conn.execute("SELECT * FROM manufacturing_equipment ORDER BY id")]


def save_bom_version(nm_id: int, materials: list[dict], operations: list[dict] | None = None,
                     packaging_per_unit: float = 0, other_per_unit: float = 0, note: str = "",
                     chrt_id: int | None = None) -> dict:
    nm_id = _integer(nm_id, "Артикул")
    chrt_id = _variant_id(chrt_id) if chrt_id is not None else 0
    packaging = _number(packaging_per_unit, "Упаковка")
    other = _number(other_per_unit, "Прочие затраты")
    if not materials or len(materials) > 100 or len(operations or []) > 100:
        raise ValueError("Нужно 1–100 компонентов и не более 100 операций")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        components, steps, keys, names = [], [], set(), set()
        visited_route_depths: dict[tuple[int, int], int] = {}
        for item in materials:
            quantity = _number(item.get("quantity_per_unit"), "Норма", positive=True)
            if round(quantity, 6) < 0.001:
                raise ValueError("Норма должна быть >= 0.001")
            component_type = str(item.get("component_type") or "raw_material")
            if component_type == "semi_finished":
                if item.get("material_name") or item.get("material_key"):
                    raise ValueError("Полуфабрикат нельзя одновременно задать как сырьё")
                component_nm_id = _integer(item.get("nm_id"), "Артикул полуфабриката")
                component_chrt_id = _variant_id(item.get("chrt_id", 0))
                identity = ("semi_finished", component_nm_id, component_chrt_id)
                if identity in keys:
                    raise ValueError("Повторяющиеся компоненты объедините")
                from .wip import validate_catalog_identity
                validate_catalog_identity(conn, component_nm_id, component_chrt_id)
                child_bom = _bom_for_component(conn, component_nm_id, component_chrt_id)
                if child_bom is None:
                    raise ValueError(f"Спецификация полуфабриката {component_nm_id} / {component_chrt_id} не найдена")
                _assert_acyclic_bom_route(conn, component_nm_id, component_chrt_id,
                                          (nm_id, chrt_id), set(), visited_depths=visited_route_depths)
                unit = str(item.get("unit") or "шт")
                if unit != "шт":
                    raise ValueError("Единица полуфабриката должна быть шт")
                keys.add(identity)
                components.append({"component_type":"semi_finished","nm_id":component_nm_id,
                                   "chrt_id":component_chrt_id,"component_bom_version_id":int(child_bom["id"]),
                                   "quantity_per_unit":quantity,"unit":"шт"})
                continue
            if component_type != "raw_material":
                raise ValueError("Тип компонента должен быть raw_material или semi_finished")
            if item.get("nm_id") is not None or item.get("chrt_id") is not None:
                raise ValueError("Для сырья не указывают артикул или вариант товара")
            name = _text(item.get("material_name"), "Материал")
            key = name.casefold()
            identity = ("raw_material", key)
            if identity in keys:
                raise ValueError("Повторяющиеся компоненты объедините")
            row = conn.execute("SELECT * FROM material_inventory_color WHERE material_key=?", (key,)).fetchone()
            if row is None:
                raise ValueError(f"Материал «{name}» отсутствует в справочнике")
            unit = str(row["unit"] or "м")
            if item.get("unit", unit) != unit:
                raise ValueError(f"Единица материала «{name}» должна быть {unit}")
            keys.add(identity)
            components.append({"component_type":"raw_material","material_key":key,
                               "material_name":row["material_name"],"quantity_per_unit":quantity,"unit":unit})
        for item in operations or []:
            name = _text(item.get("name"), "Операция")
            if name.casefold() in names:
                raise ValueError("Названия операций должны быть уникальны")
            names.add(name.casefold())
            step = {"name":name,"rate_per_unit":_number(item.get("rate_per_unit",0), "Расценка")}
            if item.get("equipment_id") is not None:
                equipment_id = _integer(item["equipment_id"], "Оборудование")
                if conn.execute("SELECT 1 FROM manufacturing_equipment WHERE id=?",(equipment_id,)).fetchone() is None:
                    raise ValueError("Оборудование не найдено")
                step.update(equipment_id=equipment_id, equipment_usage_per_unit=_number(item.get("equipment_usage_per_unit"), "Ресурс на единицу", positive=True))
            steps.append(step)
        version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM manufacturing_boms WHERE nm_id=? AND chrt_id=?", (nm_id,chrt_id)).fetchone()[0]
        cur = conn.execute("INSERT INTO manufacturing_boms(nm_id,chrt_id,version,materials_json,operations_json,packaging_per_unit,other_per_unit,note,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (nm_id,chrt_id,version,_json(components),_json(steps),packaging,other,str(note or "").strip(),_now()))
        return _decode(conn.execute("SELECT * FROM manufacturing_boms WHERE id=?",(cur.lastrowid,)).fetchone())


def list_bom_versions(nm_id: int | None = None, chrt_id: int | None = None) -> list[dict]:
    with connect() as conn:
        where, params = [], []
        if nm_id is not None:
            where.append("nm_id=?")
            params.append(_integer(nm_id,"Артикул"))
        if chrt_id is not None:
            where.append("chrt_id=?")
            params.append(_integer(chrt_id,"Вариант"))
        rows = conn.execute("SELECT * FROM manufacturing_boms" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY nm_id,chrt_id,version DESC", tuple(params))
        return [_decode(row) for row in rows]


def post_piecework(source_key: str, nm_id: int, operation_name: str, worker: str,
                   quantity: float, rate_per_unit: float, work_date: str, event_key: str,
                   chrt_id: int | None = None) -> dict:
    fields = dict(source_key=_text(source_key,"Задание"),nm_id=_integer(nm_id,"Артикул"),
                  chrt_id=_variant_id(chrt_id) if chrt_id is not None else 0,
                  operation_name=_text(operation_name,"Операция"),worker=_text(worker,"Исполнитель"),
                  quantity=_number(quantity,"Количество",positive=True),rate_per_unit=_number(rate_per_unit,"Расценка"),
                  work_date=_day(work_date),event_key=_text(event_key,"Ключ операции"))
    digest = _hash(fields)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM manufacturing_piecework WHERE event_key=?",(fields["event_key"],)).fetchone()
        if old:
            if old["request_hash"] != digest:
                raise ValueError("Этот ключ уже использован для другой операции")
            return _decode(old)
        if conn.execute(
            "SELECT 1 FROM manufacturing_releases WHERE source_key=? OR substr(source_key,1,length(?)+6)=?||'|chrt:'",
            (fields["source_key"],fields["source_key"],fields["source_key"]),
        ).fetchone():
            raise ValueError("Выпуск уже проведён: нельзя менять его трудозатраты")
        conn.execute("INSERT INTO manufacturing_piecework(source_key,nm_id,chrt_id,operation_name,worker,quantity,rate_per_unit,work_date,event_key,request_hash,created_at) VALUES (:source_key,:nm_id,:chrt_id,:operation_name,:worker,:quantity,:rate_per_unit,:work_date,:event_key,:request_hash,:created_at)", {**fields,"request_hash":digest,"created_at":_now()})
        return _decode(conn.execute("SELECT * FROM manufacturing_piecework WHERE event_key=?",(fields["event_key"],)).fetchone())


def list_piecework() -> list[dict]:
    with connect() as conn:
        return [{**_decode(row), "amount_rub":round(row["quantity"]*row["rate_per_unit"],2)} for row in conn.execute("SELECT * FROM manufacturing_piecework ORDER BY id DESC")]


def list_material_catalog() -> list[dict]:
    with connect() as conn:
        return [{**dict(r),"quantity":float(r["full_rolls"] or 0)*float(r["roll_length"] or 0)+float(r["partial_meters"] or 0)} for r in conn.execute("SELECT * FROM material_inventory_color ORDER BY material_name")]


def list_material_layers() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT l.*,COALESCE(m.unit,'м') AS unit,l.remaining_meters AS remaining_quantity,l.unit_cost_rub_m AS unit_cost_rub FROM material_cost_layers l LEFT JOIN material_inventory_color m USING(material_key) WHERE l.status='active' AND l.remaining_meters>0 ORDER BY l.source_date,l.id")]


def _change_physical(conn: sqlite3.Connection, key: str, quantity: float, unit: str) -> None:
    row = conn.execute("SELECT * FROM material_inventory_color WHERE material_key=?",(key,)).fetchone()
    if row is None or not row["balance_known"] or str(row["unit"] or "м") != unit:
        raise ValueError("Подтвердите остаток и единицу материала")
    current = float(row["full_rolls"])*float(row["roll_length"])+float(row["partial_meters"])
    remaining = round(current + quantity, 6)
    if remaining < -0.000001:
        raise ValueError(f"Недостаточно сырья «{row['material_name']}»")
    remaining = max(0, remaining)
    length = float(row["roll_length"])
    rolls = int(remaining // length) if row["tracking_mode"] == "packaged" and length > 0 else 0
    conn.execute("UPDATE material_inventory_color SET full_rolls=?,partial_meters=?,updated_at=? WHERE material_key=?",(rolls,round(remaining-rolls*length,6),_now(),key))


def _movement(conn, *, event_key, movement_type, nm_id=0, quantity=0, source_key="", day, ready_delta=0, note="") -> int:
    cur = conn.execute("INSERT INTO inventory_movements(event_key,movement_type,nm_id,quantity,ready_delta,inbound_delta,source_task_key,movement_date,status,note,created_at,material_delta) VALUES (?,?,?,?,?,0,?,?, 'applied',?,?,0)", (event_key,movement_type,nm_id,quantity,ready_delta,source_key,day,note,_now()))
    return int(cur.lastrowid)


def _post_release_conn(conn, *, nm_id, quantity, production_date, source_key,
                       bom_version_id=None, supplier_article="", product_name="", chrt_id=None) -> dict:
    from .fifo_materials import _consume_material_fifo
    from .fifo_finished_goods import _create_finished_goods_layer, _move_finished_goods_fifo
    from .production import _apply_pipeline_delta
    nm_id, quantity = _integer(nm_id,"Артикул"), _integer(quantity,"Количество выпуска")
    chrt_id = _variant_id(chrt_id) if chrt_id is not None else 0
    day, key = _day(production_date), _text(source_key,"Задание")
    requested_version = _integer(bom_version_id,"Версия BOM") if bom_version_id is not None else None
    # Preserve the old key for legacy callers. Variant suffixes keep historical
    # rows readable, but the caller's base task key remains globally single-use:
    # a retry routed to another variant must never create a second release.
    release_key = key if chrt_id == 0 else f"{key}|chrt:{chrt_id}"
    digest = _hash([nm_id,chrt_id,quantity,day,key,requested_version,str(supplier_article),str(product_name)])
    old = conn.execute("SELECT * FROM manufacturing_releases WHERE source_key=?",(release_key,)).fetchone()
    if old:
        if old["request_hash"] != digest or old["status"] != "applied":
            raise ValueError("Ключ выпуска уже использован; для нового выпуска нужен новый ключ")
        # Idempotent retries must return the same typed FIFO provenance as the
        # first response and the journal readback.
        return {**_hydrate_semi_finished_provenance(conn,_decode(old)),"posted":0,"skipped":1,"units":0,"cost_rub":0}
    variant_prefix = f"{key}|chrt:"
    existing_keys = conn.execute(
        "SELECT source_key FROM manufacturing_releases WHERE source_key=? OR substr(source_key,1,?)=?",
        (key, len(variant_prefix), variant_prefix),
    ).fetchall()
    if existing_keys:
        raise ValueError("Ключ задания уже использован для другого варианта; для нового выпуска нужен новый ключ")
    if conn.execute("SELECT 1 FROM inventory_movements WHERE source_task_key=? AND movement_type IN ('production_receipt','production_receipt_wip') AND reversed_at IS NULL",(release_key,)).fetchone():
        raise ValueError("По заданию уже есть производственная проводка")
    if requested_version:
        bom = conn.execute("SELECT * FROM manufacturing_boms WHERE nm_id=? AND id=?", (nm_id,requested_version)).fetchone()
        if bom is not None:
            bom_chrt_id = int(bom["chrt_id"] or 0)
            if (chrt_id == 0 and bom_chrt_id != 0) or (chrt_id > 0 and bom_chrt_id not in (0, chrt_id)):
                raise ValueError("Версия BOM относится к другому варианту")
    else:
        bom = conn.execute("SELECT * FROM manufacturing_boms WHERE nm_id=? AND chrt_id=? ORDER BY version DESC LIMIT 1", (nm_id,chrt_id)).fetchone()
        if bom is None and chrt_id:
            # A legacy NM-level BOM may serve a variant only when that variant
            # has no own specification.
            bom = conn.execute("SELECT * FROM manufacturing_boms WHERE nm_id=? AND chrt_id=0 ORDER BY version DESC LIMIT 1", (nm_id,)).fetchone()
    if bom is None:
        raise ValueError("Спецификация для артикула не найдена")
    spec = _decode(bom)
    components, labor, equipment = [], [], []
    for item in spec["materials"]:
        needed = round(item["quantity_per_unit"]*quantity,6)
        if item.get("component_type") == "semi_finished":
            if not float(needed).is_integer():
                raise ValueError("Количество полуфабриката должно быть целым")
            needed_units = int(needed)
            available = conn.execute(
                "SELECT COALESCE(SUM(ready_units),0) FROM finished_goods_cost_layers "
                "WHERE nm_id=? AND chrt_id=? AND status<>'reversed' AND ready_units>0 "
                "AND (source_date IS NULL OR source_date<=?)",
                (int(item["nm_id"]),int(item["chrt_id"]),day),
            ).fetchone()[0]
            if int(available or 0) < needed_units:
                raise ValueError(f"Недостаточно FIFO-полуфабрикатов {item['nm_id']} / {item['chrt_id']}")
            components.append({**item,"quantity":needed,"required_units":needed_units})
        else:
            available = conn.execute("SELECT COALESCE(SUM(remaining_meters),0) FROM material_cost_layers WHERE material_key=? AND status='active' AND (source_date IS NULL OR source_date<=?)",(item["material_key"],day)).fetchone()[0]
            if available + 0.000001 < needed:
                raise ValueError(f"Недостаточно FIFO-слоёв «{item['material_name']}»")
            components.append({**item,"quantity":needed})
    actuals = [dict(r) for r in conn.execute(
        "SELECT * FROM manufacturing_piecework WHERE source_key=? AND chrt_id IN (?,0) ORDER BY CASE WHEN chrt_id=? THEN 0 ELSE 1 END,id",
        (key,chrt_id,chrt_id),
    )]
    if chrt_id and any(int(row["chrt_id"] or 0) == chrt_id for row in actuals):
        actuals = [row for row in actuals if int(row["chrt_id"] or 0) == chrt_id]
    if any(r["nm_id"] != nm_id or r["work_date"] > day for r in actuals):
        raise ValueError("Проверьте артикул и дату сдельных операций")
    known_operations = {op["name"] for op in spec["operations"]}
    if any(row["operation_name"] not in known_operations for row in actuals):
        raise ValueError("Сдельная операция отсутствует в выбранной спецификации")
    for op in spec["operations"]:
        booked = [row for row in actuals if row["operation_name"] == op["name"]]
        if booked:
            labor.extend({"operation_name":r["operation_name"],"worker":r["worker"],"quantity":r["quantity"],"rate_per_unit":r["rate_per_unit"],"amount_rub":round(r["quantity"]*r["rate_per_unit"],2),"source":"actual","piecework_id":r["id"]} for r in booked)
        else:
            labor.append({"operation_name":op["name"],"worker":"","quantity":quantity,"rate_per_unit":op["rate_per_unit"],"amount_rub":round(quantity*op["rate_per_unit"],2),"source":"bom_norm"})
        if "equipment_id" in op:
            machine = conn.execute("SELECT * FROM manufacturing_equipment WHERE id=?",(op["equipment_id"],)).fetchone()
            if machine is None:
                raise ValueError("Оборудование спецификации не найдено")
            for later in conn.execute("SELECT equipment_json FROM manufacturing_releases WHERE production_date>? AND status='applied'",(day,)):
                if any(entry["equipment_id"] == machine["id"] for entry in json.loads(later[0])):
                    raise ValueError("На оборудовании есть более поздний выпуск: проведите выпуски по порядку дат")
            usage = op["equipment_usage_per_unit"] * quantity
            previous_usage = sum(e["usage"] for e in equipment if e["equipment_id"] == machine["id"])
            depreciable_usage = min(usage,max(0,machine["resource_quantity"]-machine["consumed_resource"]-previous_usage))
            rate = (machine["acquisition_cost_rub"]-machine["salvage_value_rub"])/machine["resource_quantity"]
            prior = min(machine["resource_quantity"],machine["consumed_resource"]+previous_usage)
            charge = round(round((prior+depreciable_usage)*rate,2)-round(prior*rate,2),2)
            equipment.append({"equipment_id":machine["id"],"name":machine["name"],"operation_name":op["name"],"resource_unit":machine["resource_unit"],"usage":usage,"depreciable_usage":depreciable_usage,"rate_per_resource_unit":rate,"amount_rub":charge})
    movement_id = _movement(conn,event_key="manufacturing:"+release_key,movement_type="production_receipt",nm_id=nm_id,quantity=quantity,source_key=release_key,day=day,ready_delta=quantity,note=f"BOM v{bom['version']}" + (f"; chrt_id {chrt_id}" if chrt_id else ""))
    conn.execute("UPDATE inventory_movements SET chrt_id=? WHERE id=?", (chrt_id,movement_id))
    material_total = 0.0
    for item in components:
        if item.get("component_type") == "semi_finished":
            catalog = conn.execute("SELECT supplier_article,product_name FROM products_catalog WHERE nm_id=?",
                                   (int(item["nm_id"]),)).fetchone()
            cost, allocations = _move_finished_goods_fifo(
                conn,movement_id,int(item["nm_id"]),str(catalog["supplier_article"] or ""),
                str(catalog["product_name"] or ""),int(item["required_units"]),"ready","out",
                chrt_id=int(item["chrt_id"]),
            )
            from .production import _apply_pipeline_delta
            _apply_pipeline_delta(conn,int(item["nm_id"]),str(catalog["supplier_article"] or ""),
                                  -int(item["required_units"]),0)
        else:
            _change_physical(conn,item["material_key"],-item["quantity"],item["unit"])
            cost, allocations = _consume_material_fifo(conn,movement_id,item["material_key"],item["material_name"],item["quantity"])
        item.update(cost_rub=cost,allocations=allocations)
        material_total += cost
    labor_total = round(sum(r["amount_rub"] for r in labor),2)
    depreciation = round(sum(r["amount_rub"] for r in equipment),2)
    packaging, other = round(spec["packaging_per_unit"]*quantity,2),round(spec["other_per_unit"]*quantity,2)
    total = round(material_total+labor_total+depreciation+packaging+other,2)
    unit_cost = round(total/quantity,6)
    _apply_pipeline_delta(conn,nm_id,str(supplier_article),quantity,0)
    layer_id = _create_finished_goods_layer(conn,nm_id,str(supplier_article),str(product_name),"production",f"movement:{movement_id}",day,quantity,unit_cost,"ready",f"BOM v{bom['version']}" + (f"; chrt_id {chrt_id}" if chrt_id else ""),chrt_id=chrt_id)
    conn.execute("UPDATE inventory_movements SET supplier_article=?,product_name=?,material_cost_rub=?,unit_cost_rub=?,goods_cost_rub=?,goods_unit_cost_rub=? WHERE id=?",(str(supplier_article),str(product_name),material_total,unit_cost,total,unit_cost,movement_id))
    conn.execute("INSERT INTO production_cost_batches(movement_id,nm_id,supplier_article,product_name,batch_date,produced_units,material_key,material_name,material_meters,material_cost_rub,packaging_cost_rub,labor_cost_rub,other_cost_rub,total_cost_rub,unit_cost_rub,chrt_id,status,note,created_at) VALUES (?,?,?,?,?,?,'','BOM',0,?,?,?,?,?,?,?,'active',?,?)",(movement_id,nm_id,str(supplier_article),str(product_name),day,quantity,material_total,packaging,labor_total,other+depreciation,total,unit_cost,chrt_id,f"BOM v{bom['version']}; амортизация {depreciation}",_now()))
    for entry in equipment:
        conn.execute("UPDATE manufacturing_equipment SET consumed_resource=consumed_resource+?,updated_at=? WHERE id=?",(entry["usage"],_now(),entry["equipment_id"]))
    if actuals:
        conn.execute(
            f"UPDATE manufacturing_piecework SET release_movement_id=? WHERE id IN ({','.join('?' for _ in actuals)})",
            (movement_id, *(int(row["id"]) for row in actuals)),
        )
    release_cursor = conn.execute("INSERT INTO manufacturing_releases(movement_id,source_key,request_hash,nm_id,chrt_id,quantity,production_date,bom_version_id,materials_json,labor_json,equipment_json,material_cost_rub,labor_cost_rub,depreciation_cost_rub,packaging_cost_rub,other_cost_rub,total_cost_rub,unit_cost_rub,finished_layer_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(movement_id,release_key,digest,nm_id,chrt_id,quantity,day,bom["id"],_json(components),_json(labor),_json(equipment),material_total,labor_total,depreciation,packaging,other,total,unit_cost,layer_id,_now()))
    release_id = int(release_cursor.lastrowid)
    conn.execute("UPDATE finished_goods_cost_layers SET producer_release_id=? WHERE id=?", (release_id,layer_id))
    release = _decode(conn.execute("SELECT * FROM manufacturing_releases WHERE id=?",(release_id,)).fetchone())
    return {**_hydrate_semi_finished_provenance(conn,release),"posted":1,"skipped":0,"units":quantity,"cost_rub":total}


def post_manufacturing_release(nm_id: int, quantity: int, production_date: str, source_key: str,
                               bom_version_id: int | None = None, supplier_article: str = "", product_name: str = "",
                               chrt_id: int | None = None) -> dict:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return _post_release_conn(conn,nm_id=nm_id,quantity=quantity,production_date=production_date,source_key=source_key,bom_version_id=bom_version_id,supplier_article=supplier_article,product_name=product_name,chrt_id=chrt_id)


def list_manufacturing_releases(nm_id: int | None = None) -> list[dict]:
    with connect() as conn:
        return [_hydrate_semi_finished_provenance(conn,_decode(r)) for r in conn.execute("SELECT * FROM manufacturing_releases" + (" WHERE nm_id=?" if nm_id is not None else "") + " ORDER BY id DESC",(() if nm_id is None else (_integer(nm_id,"Артикул"),)))]


def write_off_material_defect(layer_id: int, quantity: float, event_key: str, reason: str, movement_date: str) -> dict:
    layer_id, quantity = _integer(layer_id,"Партия сырья"), _number(quantity,"Количество брака",positive=True)
    key, reason, day = _text(event_key,"Ключ операции"),_text(reason,"Причина брака"),_day(movement_date)
    if quantity < 0.001:
        raise ValueError("Количество брака должно быть >= 0.001")
    digest = _hash([layer_id,quantity,key,reason,day])
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM manufacturing_defects WHERE event_key=?",(key,)).fetchone()
        if old:
            if old["request_hash"] != digest or old["status"] != "applied":
                raise ValueError("Ключ уже использован другой или отменённой операцией")
            return _decode(old)
        layer = conn.execute("SELECT l.*,COALESCE(m.unit,'м') AS unit FROM material_cost_layers l LEFT JOIN material_inventory_color m USING(material_key) WHERE l.id=?",(layer_id,)).fetchone()
        if layer is None or layer["status"] != "active" or layer["remaining_meters"] + 0.000001 < quantity:
            raise ValueError("Недостаточно сырья в выбранной FIFO-партии")
        if layer["source_date"] and day < str(layer["source_date"])[:10]:
            raise ValueError("Дата брака не может предшествовать партии")
        _change_physical(conn,layer["material_key"],-quantity,layer["unit"])
        cost = round(quantity*layer["unit_cost_rub_m"],2)
        movement_id = _movement(conn,event_key="material_defect:"+key,movement_type="material_defect",day=day,note=reason)
        remaining = round(layer["remaining_meters"]-quantity,6)
        conn.execute("UPDATE material_cost_layers SET remaining_meters=?,status=?,updated_at=? WHERE id=?",(remaining,"depleted" if remaining <= 0.0005 else "active",_now(),layer_id))
        conn.execute("INSERT INTO material_fifo_consumptions(movement_id,layer_id,meters,amount_rub,status,created_at) VALUES (?,?,?,?,'applied',?)",(movement_id,layer_id,quantity,cost,_now()))
        conn.execute("UPDATE inventory_movements SET material_key=?,material_name=?,material_cost_rub=? WHERE id=?",(layer["material_key"],layer["material_name"],cost,movement_id))
        conn.execute("INSERT INTO manufacturing_defects(movement_id,layer_id,material_key,material_name,quantity,unit,cost_rub,reason,movement_date,event_key,request_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(movement_id,layer_id,layer["material_key"],layer["material_name"],quantity,layer["unit"],cost,reason,day,key,digest,_now()))
        return _decode(conn.execute("SELECT * FROM manufacturing_defects WHERE movement_id=?",(movement_id,)).fetchone())


def list_material_defects() -> list[dict]:
    with connect() as conn:
        return [_decode(r) for r in conn.execute("SELECT * FROM manufacturing_defects ORDER BY id DESC")]


def _undo_manufacturing_conn(conn: sqlite3.Connection, movement_id: int) -> dict | None:
    """Called by the generic undo dispatcher, in its existing transaction."""
    from .fifo_materials import _reverse_fifo_consumption
    from .fifo_finished_goods import _reverse_finished_goods_source_layer
    from .production import _apply_pipeline_delta
    release = conn.execute("SELECT * FROM manufacturing_releases WHERE movement_id=?",(movement_id,)).fetchone()
    defect = conn.execute("SELECT * FROM manufacturing_defects WHERE movement_id=?",(movement_id,)).fetchone()
    row = release or defect
    if row is None:
        return None
    if row["status"] != "applied":
        return {"ok":False,"message":"Операция уже отменена"}
    conn.execute("SAVEPOINT undo_manufacturing")
    try:
        if release:
            data = _decode(release)
            # A newer equipment snapshot depends on consumed lifetime. Undo in reverse order.
            for item in data["equipment"]:
                for later in conn.execute("SELECT equipment_json FROM manufacturing_releases WHERE id>? AND status='applied'",(row["id"],)):
                    if any(e["equipment_id"] == item["equipment_id"] for e in json.loads(later[0])):
                        raise ValueError("Сначала отмените более поздние выпуски на этом оборудовании")
            _reverse_finished_goods_source_layer(conn,movement_id)
            from .fifo_finished_goods import _reverse_finished_goods_allocations
            _reverse_finished_goods_allocations(conn,movement_id)
            _reverse_fifo_consumption(conn,movement_id)
            for component in data["materials"]:
                if component.get("component_type") == "semi_finished":
                    from .production import _apply_pipeline_delta
                    _apply_pipeline_delta(conn,int(component["nm_id"]),"",
                                          int(component["required_units"]),0)
                else:
                    _change_physical(conn,component["material_key"],component["quantity"],component["unit"])
            _apply_pipeline_delta(conn,row["nm_id"],"",-row["quantity"],0)
            for item in data["equipment"]:
                conn.execute("UPDATE manufacturing_equipment SET consumed_resource=MAX(0,consumed_resource-?),updated_at=? WHERE id=?",(item["usage"],_now(),item["equipment_id"]))
            conn.execute("UPDATE production_cost_batches SET status='reversed' WHERE movement_id=?",(movement_id,))
            conn.execute("UPDATE manufacturing_releases SET status='reversed',reversed_at=? WHERE movement_id=?",(_now(),movement_id))
            # Preserve piecework link as immutable audit history; repost requires a new job.
        else:
            _reverse_fifo_consumption(conn,movement_id)
            _change_physical(conn,row["material_key"],row["quantity"],row["unit"])
            conn.execute("UPDATE manufacturing_defects SET status='reversed',reversed_at=? WHERE movement_id=?",(_now(),movement_id))
        conn.execute("UPDATE inventory_movements SET status='reversed',reversed_at=? WHERE id=?",(_now(),movement_id))
        conn.execute("RELEASE SAVEPOINT undo_manufacturing")
        return {"ok":True,"message":"Операция отменена, все остатки и ресурс оборудования восстановлены"}
    except Exception as exc:
        conn.execute("ROLLBACK TO SAVEPOINT undo_manufacturing")
        conn.execute("RELEASE SAVEPOINT undo_manufacturing")
        return {"ok":False,"message":str(exc)}
