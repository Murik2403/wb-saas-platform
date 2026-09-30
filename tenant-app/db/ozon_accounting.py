"""Tenant-local Ozon ledger, isolated from WB sales and FIFO movements."""
from __future__ import annotations
from datetime import datetime, timezone, date
from decimal import Decimal, InvalidOperation
import json
import hashlib
from .core import connect


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def ensure_schema(conn):
    for sql in (
        "CREATE TABLE IF NOT EXISTS ozon_accounting_snapshots (period_key TEXT PRIMARY KEY,start_date TEXT NOT NULL,end_date TEXT NOT NULL,created_at TEXT NOT NULL,payload_json TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS ozon_accrual_ledger (source_key TEXT PRIMARY KEY,accrual_id TEXT NOT NULL,accrual_date TEXT NOT NULL,currency TEXT NOT NULL,amount_text TEXT NOT NULL,payload_json TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS ozon_sku_costs (sku TEXT PRIMARY KEY,mode TEXT NOT NULL,unit_cost_text TEXT,nm_id INTEGER,chrt_id INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS ozon_cost_mapping_history (id INTEGER PRIMARY KEY AUTOINCREMENT,sku TEXT NOT NULL,before_json TEXT,after_json TEXT NOT NULL,changed_at TEXT NOT NULL)",
    ):
        conn.execute(sql)


def _money(value):
    if not isinstance(value, dict) or not isinstance(value.get("currency"), str) or isinstance(value.get("amount"), bool):
        raise ValueError("Некорректный денежный формат Ozon.")
    try:
        amount = Decimal(str(value["amount"]))
    except (InvalidOperation, KeyError):
        raise ValueError("Некорректная сумма Ozon.") from None
    if not amount.is_finite():
        raise ValueError("Неконечная сумма Ozon.")
    return amount, value["currency"]


def import_snapshot(snapshot):
    start, end = snapshot["start_date"], snapshot["end_date"]
    prepared = []
    occurrences = {}
    for row in snapshot["accruals"]:
        identifier = row.get("accrual_id")
        if isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or not str(identifier):
            raise ValueError("Начисление Ozon не содержит accrual_id.")
        day = str(row.get("date", ""))[:10]
        date.fromisoformat(day)
        if not start <= day <= end:
            raise ValueError("Ozon вернул начисление вне выбранного периода.")
        amount, currency = _money(row.get("total_amount"))
        # Source ID uniqueness is not assumed. Retain every occurrence in each
        # completed day and replace that window atomically on subsequent imports.
        payload = canonical(row)
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        identity = day + ":" + fingerprint
        occurrence = occurrences.get(identity, 0) + 1
        occurrences[identity] = occurrence
        key = "ozon:day:" + identity + ":" + str(occurrence)
        prepared.append((key, str(identifier), day, currency, str(amount), payload))
    snapshot = {**snapshot, "identical_row_occurrences": sum(v - 1 for v in occurrences.values())}
    encoded = canonical(snapshot)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ensure_schema(conn)
        from .ozon_pnl import capture_source_snapshot
        capture_source_snapshot(conn, snapshot)
        # Source range is replaced atomically after all remote collections succeed.
        conn.execute("DELETE FROM ozon_accrual_ledger WHERE accrual_date BETWEEN ? AND ?", (start, end))
        conn.executemany("INSERT INTO ozon_accrual_ledger VALUES (?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET accrual_id=excluded.accrual_id,accrual_date=excluded.accrual_date,currency=excluded.currency,amount_text=excluded.amount_text,payload_json=excluded.payload_json", prepared)
        conn.execute("INSERT INTO ozon_accounting_snapshots VALUES (?,?,?,?,?) ON CONFLICT(period_key) DO UPDATE SET created_at=excluded.created_at,payload_json=excluded.payload_json", (start+":"+end,start,end,datetime.now(timezone.utc).isoformat(),encoded))
    return {"imported": len(prepared), "duplicates": 0, "identical_row_occurrences": snapshot["identical_row_occurrences"]}


def save_cost_mapping(sku, mode, unit_cost=None, nm_id=None, chrt_id=0):
    if not str(sku).isdigit() or int(sku) <= 0:
        raise ValueError("Укажите SKU Ozon.")
    if mode not in {"manual", "wb_estimate"}:
        raise ValueError("Неизвестный источник себестоимости.")
    cost = None
    if mode == "manual":
        try:
            cost = Decimal(str(unit_cost))
        except InvalidOperation:
            raise ValueError("Проверьте себестоимость.") from None
        if not cost.is_finite() or cost < 0 or cost > Decimal("1000000000"):
            raise ValueError("Проверьте себестоимость.")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE"); ensure_schema(conn)
        if mode == "wb_estimate" and (not nm_id or not conn.execute("SELECT 1 FROM costs WHERE nm_id=?", (nm_id,)).fetchone()):
            raise ValueError("У выбранного товара WB нет себестоимости.")
        before = conn.execute("SELECT * FROM ozon_sku_costs WHERE sku=?", (str(sku),)).fetchone()
        now = datetime.now(timezone.utc).isoformat()
        after = {"sku": str(sku), "mode": mode, "unit_cost_text": str(cost) if cost is not None else None, "nm_id": nm_id if mode == "wb_estimate" else None, "chrt_id": chrt_id}
        prior = {k: before[k] for k in after} if before else None
        if prior == after:
            return
        conn.execute("INSERT INTO ozon_sku_costs VALUES (?,?,?,?,?,?) ON CONFLICT(sku) DO UPDATE SET mode=excluded.mode,unit_cost_text=excluded.unit_cost_text,nm_id=excluded.nm_id,chrt_id=excluded.chrt_id,updated_at=excluded.updated_at", (after["sku"], mode, after["unit_cost_text"], after["nm_id"], chrt_id, now))
        conn.execute("INSERT INTO ozon_cost_mapping_history(sku,before_json,after_json,changed_at) VALUES (?,?,?,?)", (str(sku),canonical(prior) if prior else None,canonical(after),now))


def workspace(start, end):
    with connect() as conn:
        ensure_schema(conn)
        saved = conn.execute("SELECT created_at,payload_json FROM ozon_accounting_snapshots WHERE period_key=?", (start+":"+end,)).fetchone()
        mappings = {row["sku"]: dict(row) for row in conn.execute("SELECT * FROM ozon_sku_costs")}
        wb_costs = {row["nm_id"]: Decimal(str(row["cost_per_wb_unit"] or 0)) for row in conn.execute("SELECT nm_id,cost_per_wb_unit FROM costs")}
    if not saved:
        return {"loaded": False, "source_complete": False, "accounting_complete": False, "period": {"start_date": start,"end_date": end}, "products": [], "net_accrual_rub": None, "profit_rub": None, "blockers": ["Данные Ozon за выбранный период ещё не загружены."]}
    data = json.loads(saved["payload_json"])
    types = {str(row.get("id")): row for row in data["types"]}
    net = Decimal(0); cogs = Decimal(0); sales_units = 0; covered_units = 0
    unknown_types = set(); blockers = set(); fees = {}; unknown_categories = set()
    if data.get("identical_row_occurrences"):
        blockers.add("В источнике есть одинаковые строки начислений: все сохранены, уникальность операций не подтверждена.")
    def walk(value):
        if isinstance(value, dict):
            if "type_id" in value:
                identifier = str(value["type_id"])
                if identifier not in types: unknown_types.add(identifier)
                if "accrued" in value:
                    try:
                        amount, currency = _money(value["accrued"])
                        if currency != "RUB": blockers.add("Есть начисления не в RUB; автоматическая конвертация не выполнялась.")
                        else: fees[identifier] = fees.get(identifier, Decimal(0)) + amount
                    except ValueError: blockers.add("Не распознан денежный формат компонента начисления.")
                else:
                    blockers.add("У компонента сбора отсутствует сумма начисления.")
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    for row in data["accruals"]:
        amount, currency = _money(row["total_amount"])
        if currency == "RUB": net += amount
        else: blockers.add("Есть начисления не в RUB; автоматическая конвертация не выполнялась.")
        category = row.get("accrued_category")
        if category not in {"POSTING", "ITEM", "NON_ITEM", "CONTAINER"}: unknown_categories.add(str(category))
        walk(row)
        if row.get("container_fees"):
            blockers.add("Начисления по грузоместам сохранены; их структура пока не подтверждена.")
        posting=row.get("posting")
        product_rows=posting.get("products") if isinstance(posting,dict) else None
        if category in {"POSTING","ITEM"} and (not isinstance(product_rows,list) or not product_rows):
            blockers.add("Товарное начисление без подтверждённого состава: объём и себестоимость неизвестны.")
        if not isinstance(product_rows,list):
            product_rows=[]
        for item in product_rows:
            if not isinstance(item,dict):
                blockers.add("Не распознан состав товарного начисления Ozon.")
                continue
            try:
                qty = int(item["quantity"])
                if qty <= 0 or isinstance(item["quantity"], bool) or qty != item["quantity"]: raise ValueError()
                sale, sale_currency = _money(item["commission"]["sale_amount"])
                if sale_currency != "RUB": raise ValueError()
            except (KeyError, TypeError, ValueError):
                blockers.add("Не распознан объём продажи/возврата Ozon."); continue
            if sale == 0:
                blockers.add("Нулевое начисление отправления: движение товара не подтверждено."); continue
            sales_units += qty
            mapping = mappings.get(str(item.get("sku")))
            rate = None
            if mapping:
                rate = Decimal(mapping["unit_cost_text"]) if mapping["mode"] == "manual" else wb_costs.get(mapping["nm_id"])
                if mapping["mode"] == "wb_estimate" and rate is not None and rate <= 0: rate = None
            if rate is not None and rate.is_finite() and rate >= 0:
                covered_units += qty; cogs += rate*qty*(1 if sale > 0 else -1)
    if unknown_types: blockers.add("Неизвестные типы сборов Ozon: "+", ".join(sorted(unknown_types)))
    if unknown_categories: blockers.add("Неизвестные категории начислений: "+", ".join(sorted(unknown_categories)))
    if covered_units < sales_units: blockers.add("Не для всех проданных/возвращённых единиц указана себестоимость.")
    if sales_units:
        blockers.add("Исторический FIFO Ozon не подтверждён: себестоимость сопоставлена оценочно.")
        blockers.add("Расшифровка продаж и комиссий предварительная; сверка компонентов с итогом Ozon не подтверждена.")
    stocks = {str(row.get("product_id")): row.get("stocks", []) for row in data["stocks"]}
    products = []
    for row in data["products"]:
        sku = str(row.get("sku") or "")
        products.append({"sku": sku,"offer_id": str(row.get("offer_id") or ""),"product_id": row.get("product_id"),"stocks": stocks.get(str(row.get("product_id")), []),"cost_mapping": mappings.get(sku)})
    return {"loaded": True,"source_complete": True,"accounting_complete": not blockers,"period": {"start_date": start,"end_date": end},"synced_at": saved["created_at"],"net_accrual_rub": str(net),"estimated_cogs_rub": str(cogs),"profit_rub": None,"estimate_after_known_cogs_rub": str(net-cogs) if covered_units == sales_units and not blockers else None,"sales_and_return_units": sales_units,"known_cost_units": covered_units,"exact_fifo_units": 0,"blockers": sorted(blockers),"products": products,"source_counts": {key: len(data[key]) for key in ("products","stocks","fbo","fbs","returns","accruals")},"fees": [{"type_id": key,"name": types.get(key,{}).get("description") or types.get(key,{}).get("name") or "Неизвестный сбор","amount_rub": str(value),"mapped": key in types} for key,value in sorted(fees.items())],"returns_scope": "Текущий реестр всех возвратов, не сумма возвратов выбранного периода."}
