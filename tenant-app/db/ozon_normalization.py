"""Version-bound Ozon financial drafts and explicit document/FIFO links.

Official by-day schema (inspected 2026-10-06) does not define posting sign
formula, movement dates or correction identity. Never infer these. Fees can
be normalized from their signed, reconciled branch; posting costs require an
explicit link to already documented movements. Raw revisions are replacement
read models, not additive immutable financial events.
"""
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json

from .core import connect
from .ozon_accounting import ensure_schema as accounting_schema, _collection_verified
from .ozon_fee_preview import fee_preview, _money
from .ozon_pnl import canonical, digest, ensure_schema as document_schema, project, text, MSK

MAX_DOCUMENT_ROWS = 5000
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_LINK_ROWS = 5000
MAX_LINK_BYTES = 4 * 1024 * 1024


def _fixed(value):
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def ensure_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS ozon_normalization_links (id INTEGER PRIMARY KEY,period_key TEXT NOT NULL,namespace TEXT NOT NULL,source_fingerprint TEXT NOT NULL,occurrence_key TEXT NOT NULL,document_ids_json TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(period_key,namespace,source_fingerprint,occurrence_key))")
    for verb in ("UPDATE", "DELETE"):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS ozon_normalization_links_no_{verb.lower()} BEFORE {verb} ON ozon_normalization_links BEGIN SELECT RAISE(ABORT,'Ozon normalization links are immutable'); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS ozon_normalization_links_no_replace BEFORE INSERT ON ozon_normalization_links WHEN EXISTS(SELECT 1 FROM ozon_normalization_links WHERE id=NEW.id OR (period_key=NEW.period_key AND namespace=NEW.namespace AND source_fingerprint=NEW.source_fingerprint AND occurrence_key=NEW.occurrence_key)) BEGIN SELECT RAISE(ABORT,'Ozon normalization links are immutable'); END")


def _period(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first or (last-first).days > 30:
        raise ValueError("Выберите период Ozon до 31 дня.")
    return start + ":" + end


def _observed_posting_fields(raw):
    """Display source amounts once; reference prices never become revenue."""
    result = []
    posting = raw.get("posting")
    if not isinstance(posting, dict):
        return result
    products = posting.get("products")
    if not isinstance(products, list):
        return result
    for i, item in enumerate(products[:5000]):
        if not isinstance(item, dict):
            continue
        sku = str(item["sku"]) if type(item.get("sku")) is int and item["sku"] > 0 else None
        commission = item.get("commission")
        if isinstance(commission, dict):
            for field in ("sale_amount", "commission", "sale_commission", "bonus", "coinvestment", "sale_price", "seller_price"):
                value = commission.get(field)
                if value is None:
                    continue
                try:
                    amount = _money(value)
                except ValueError:
                    result.append({"source_path": f"posting.products[{i}].commission.{field}", "sku": sku, "amount_rub": None, "reason": "Не распознана сумма в RUB."})
                    continue
                result.append({"source_path": f"posting.products[{i}].commission.{field}", "sku": sku, "amount_rub": _fixed(amount), "reference_only": field in {"sale_commission", "sale_price", "seller_price", "bonus", "coinvestment"}})
        delivery = item.get("delivery")
        if isinstance(delivery, dict) and delivery.get("total_accrued") is not None:
            try:
                amount = _money(delivery["total_accrued"])
                result.append({"source_path": f"posting.products[{i}].delivery.total_accrued", "sku": sku, "amount_rub": _fixed(amount), "reference_only": False})
            except ValueError:
                result.append({"source_path": f"posting.products[{i}].delivery.total_accrued", "sku": sku, "amount_rub": None, "reason": "Не распознана сумма в RUB."})
    return result


def preview_snapshot(snapshot, namespace="main", *, events=None, lots=None, links=None):
    namespace = text(namespace, "Реестр", 80)
    events, lots, links = events or [], lots or [], links or {}
    if len(events) > MAX_DOCUMENT_ROWS or len(lots) > MAX_DOCUMENT_ROWS or len(links) > MAX_LINK_ROWS:
        raise ValueError("Превышен безопасный объём документальной сверки Ozon.")
    rows = snapshot.get("accruals")
    if not isinstance(rows, list) or len(rows) > 50000:
        raise ValueError("Не распознана или превышена коллекция начислений Ozon.")
    source_fingerprint = digest(snapshot)
    preview_fingerprint = digest({"source": source_fingerprint, "namespace": namespace, "events": events, "lots": lots, "links": links})
    fees = fee_preview(rows, snapshot.get("types") or [])
    source_events = {e["external_event_id"]: e for e in events if e.get("namespace") == namespace}
    projection = project(lots, events)
    costs = {r["event_id"]: r for r in projection["events"] if r["namespace"] == namespace}
    used = Counter(event for selected in links.values() for event in selected)
    normalized, observed, known_cogs = Decimal(0), Decimal(0), Decimal(0)
    normalized_count, known_units, unknown_units = 0, 0, 0
    observed_valid = True
    results = []
    for index, (raw, fee) in enumerate(zip(rows, fees["rows"])):
        key = f"ozon:normalization:v1:{source_fingerprint}:{index}"
        result = {"source_occurrence_key": key, "source_row_index": index, "eligible": False,
                  "accrual_date": fee.get("date"), "category": fee.get("category"),
                  "normalized_total_rub": None, "quantity": None, "known_cogs_rub": None,
                  "document_event_ids": [], "blockers": [], "observed_components": _observed_posting_fields(raw) if isinstance(raw, dict) else []}
        try:
            if not isinstance(raw, dict):
                raise ValueError("Не распознана строка начисления.")
            if type(raw.get("accrual_id")) is not int or not 0 <= raw["accrual_id"] < 2**63:
                raise ValueError("Не распознан идентификатор начисления Ozon.")
            day = raw.get("date")
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day or not snapshot["start_date"] <= day <= snapshot["end_date"]:
                raise ValueError("Дата начисления не соответствует выбранному периоду.")
            amount = _money(raw.get("total_amount"))
            observed += amount
            result["observed_total_rub"] = _fixed(amount)
        except ValueError as exc:
            observed_valid = False
            result["blockers"].append(str(exc))
            results.append(result)
            continue
        selected = links.get(key)
        try:
            if selected:
                if len(set(selected)) != len(selected) or any(used[e] > 1 for e in selected):
                    raise ValueError("Документальная операция назначена нескольким строкам источника.")
                docs = [source_events[e] for e in selected]
                if any(d["currency"] != "RUB" for d in docs):
                    raise ValueError("Документы должны быть в RUB.")
                if any(datetime.fromisoformat(d["accrued_at"]).astimezone(MSK).date().isoformat() != raw["date"] for d in docs):
                    raise ValueError("Дата начисления документа отличается от строки API.")
                if sum((Decimal(d["source_total_rub"]) for d in docs), Decimal(0)) != amount:
                    raise ValueError("Сумма документов не совпадает с итогом строки Ozon.")
                for d in docs:
                    if sum((Decimal(d[k]) for k in ("revenue_rub", "commission_rub", "logistics_rub", "other_rub")), Decimal(0)) != Decimal(d["source_total_rub"]):
                        raise ValueError("Компоненты документа не совпадают с итогом.")
                if raw.get("accrued_category") == "POSTING":
                    if any(raw.get(field) is not None for field in ("non_item_fee", "item_fees", "container_fees")):
                        raise ValueError("Неоднозначные категории начисления Ozon.")
                    products = raw.get("posting", {}).get("products")
                    if not isinstance(products, list) or not products or any(type(p.get("sku")) is not int or p["sku"] < 1 for p in products if isinstance(p, dict)) or any(not isinstance(p, dict) for p in products):
                        raise ValueError("Не подтверждён набор SKU товарного начисления.")
                    source_skus = {str(p["sku"]) for p in products}
                    if any(d["kind"] not in {"sale", "return"} for d in docs) or {d["sku"] for d in docs} != source_skus:
                        raise ValueError("Документы должны явно покрывать SKU продаж/возвратов этой строки.")
                elif not fee["reconciled"] or any(d["kind"] != "fee" for d in docs):
                    raise ValueError("Для этой категории допустимы только сверенные документальные сборы.")
                row_cogs = Decimal(0)
                row_known, row_unknown, qty = 0, 0, 0
                for d in docs:
                    c = costs.get(d["external_event_id"])
                    if d["kind"] != "fee":
                        qty += d["quantity"]
                        if c is None:
                            raise ValueError("Не найдена проекция документального движения.")
                        if d["kind"] == "return":
                            original = source_events.get(d["return_of"])
                            if original is None or original["kind"] != "sale" or original["sku"] != d["sku"] or original["posting_ref"] != d["posting_ref"] or c["unknown_units"]:
                                raise ValueError("Возврат не имеет подтверждённой исходной продажи и полной исторической себестоимости.")
                        row_cogs += Decimal(c["known_cogs_rub"])
                        row_known += c["known_units"]
                        row_unknown += c["unknown_units"]
                result.update(source="explicit_document_link", document_event_ids=selected,
                              quantity=qty, known_cogs_rub=_fixed(row_cogs), known_cost_units=row_known,
                              unknown_cost_units=row_unknown, movement_basis="existing_documented_movement_timestamp",
                              document_components={k: _fixed(sum((Decimal(d[k]) for d in docs), Decimal(0))) for k in ("revenue_rub", "commission_rub", "logistics_rub", "other_rub")},
                              documented_products=[{"sku": d["sku"], "kind": d["kind"], "quantity": d["quantity"], "movement_at": d["movement_at"], "return_of": d["return_of"]} for d in docs if d["kind"] != "fee"])
                known_cogs += row_cogs
                known_units += row_known
                unknown_units += row_unknown
            elif fee["reconciled"]:
                result.update(source="signed_reconciled_fee_branch", components=fee["components"], quantity=0,
                              known_cogs_rub="0.00", known_cost_units=0, unknown_cost_units=0)
            else:
                raise ValueError(fee.get("reason") or "Формула товарного начисления не подтверждена: свяжите с документальными движениями.")
            result.update(eligible=True, normalized_total_rub=_fixed(amount))
            normalized += amount
            normalized_count += 1
        except (ValueError, KeyError, TypeError) as exc:
            result["blockers"].append(str(exc) if isinstance(exc, ValueError) else "Не найдены или не распознаны документальные операции.")
        results.append(result)
    all_normalized = bool(results) and normalized_count == len(results)
    return {"loaded": True, "namespace": namespace, "source_fingerprint": source_fingerprint, "fingerprint": preview_fingerprint,
            "period": {"start_date": snapshot["start_date"], "end_date": snapshot["end_date"]}, "rows": results,
            "observed_total_rub": _fixed(observed) if rows and observed_valid else None,
            "normalized_total_rub": _fixed(normalized) if normalized_count else None,
            "normalized_row_count": normalized_count, "excluded_row_count": len(results)-normalized_count,
            "all_rows_normalized": all_normalized, "known_cogs_rub": _fixed(known_cogs),
            "known_cost_units": known_units, "unknown_cost_units": unknown_units,
            "result_after_fifo_rub": _fixed(normalized-known_cogs) if all_normalized and not unknown_units else None,
            "financial_normalization_verified": False, "marketplace_source_complete": False,
            "physical_stock_mutated": False, "api_movement_inferred": False,
            "return_cost_policy": "proportional_original_sale_without_serial_identity",
            "contract_sources": ["https://docs.ozon.ru/api/seller/#operation/GetFinanceAccrualByDay", "https://docs.ozon.ru/api/seller/#operation/GetFinanceAccrualTypes", "https://docs.ozon.ru/api/seller/#operation/GetFinanceAccrualPostings"],
            "source_collection_complete": _collection_verified(snapshot, snapshot["start_date"], snapshot["end_date"]),
            "note": "По подтверждённым компонентам и явно связанным документам; без налогов и внешних расходов. Это не реальная сверка Ozon."}


def _document_rows(conn, table, namespace):
    if table not in {"ozon_document_events", "ozon_document_cost_lots"}:
        raise ValueError("Неверный документальный источник.")
    # These immutable keys are [namespace, external ID]. Filter the small
    # identity value rather than parsing foreign payloads just to find
    # their namespace. Count and byte caps precede any Python JSON decoding.
    predicate = "json_extract(source_key,'$[0]')=?"
    count, size = conn.execute(f"SELECT COUNT(*),COALESCE(SUM(length(CAST(payload_json AS BLOB))),0) FROM {table} WHERE {predicate}", (namespace,)).fetchone()
    if count > MAX_DOCUMENT_ROWS or size > MAX_DOCUMENT_BYTES:
        raise ValueError("Документальный реестр Ozon превышает безопасный объём сверки.")
    values = conn.execute(f"SELECT payload_json FROM {table} WHERE {predicate} ORDER BY id LIMIT ?", (namespace, MAX_DOCUMENT_ROWS+1)).fetchall()
    return [json.loads(row["payload_json"]) for row in values]


def documents(start, end, namespace="main"):
    namespace = text(namespace, "Реестр", 80)
    _period(start, end)
    with connect() as conn:
        conn.execute("BEGIN")
        document_schema(conn)
        rows = _document_rows(conn, "ozon_document_events", namespace)
    selected = [row for row in rows if start <= datetime.fromisoformat(row["accrued_at"]).astimezone(MSK).date().isoformat() <= end]
    fields = ("external_event_id", "namespace", "sku", "kind", "accrued_at", "posting_ref", "source_total_rub")
    return {"namespace": namespace, "lines": [{key: row[key] for key in fields} for row in selected], "bounded": True}


def _read(conn, start, end, namespace):
    accounting_schema(conn)
    document_schema(conn)
    ensure_schema(conn)
    saved = conn.execute("SELECT CASE WHEN length(CAST(payload_json AS BLOB))<=? THEN payload_json END AS payload_json,created_at FROM ozon_accounting_snapshots WHERE period_key=?", (MAX_SNAPSHOT_BYTES, _period(start, end))).fetchone()
    if saved is None:
        return None, [], [], {}
    if saved["payload_json"] is None:
        raise ValueError("Сохранённый источник Ozon превышает безопасный объём сверки.")
    if conn.execute("SELECT 1 FROM ozon_accounting_snapshots WHERE start_date<=? AND end_date>=? AND created_at>? LIMIT 1", (end, start, saved["created_at"])).fetchone():
        raise ValueError("Период устарел после перекрывающей загрузки Ozon; загрузите весь период повторно.")
    snapshot = json.loads(saved["payload_json"])
    fingerprint = digest(snapshot)
    events = _document_rows(conn, "ozon_document_events", namespace)
    lots = _document_rows(conn, "ozon_document_cost_lots", namespace)
    args = (_period(start, end), namespace, fingerprint)
    count, size = conn.execute("SELECT COUNT(*),COALESCE(SUM(length(CAST(document_ids_json AS BLOB))),0) FROM ozon_normalization_links WHERE period_key=? AND namespace=? AND source_fingerprint=?", args).fetchone()
    if count > MAX_LINK_ROWS or size > MAX_LINK_BYTES:
        raise ValueError("Связи документов Ozon превышают безопасный объём сверки.")
    links = {r["occurrence_key"]: json.loads(r["document_ids_json"]) for r in conn.execute("SELECT occurrence_key,document_ids_json FROM ozon_normalization_links WHERE period_key=? AND namespace=? AND source_fingerprint=? LIMIT ?", (*args, MAX_LINK_ROWS+1))}
    return snapshot, events, lots, links


def preview(start, end, namespace="main"):
    namespace = text(namespace, "Реестр", 80)
    with connect() as conn:
        conn.execute("BEGIN")
        snapshot, events, lots, links = _read(conn, start, end, namespace)
    if snapshot is None:
        return {"loaded": False, "rows": [], "source_fingerprint": None, "normalized_total_rub": None,
                "result_after_fifo_rub": None, "financial_normalization_verified": False, "marketplace_source_complete": False}
    return preview_snapshot(snapshot, namespace, events=events, lots=lots, links=links)


def confirm_link(start, end, namespace, expected_fingerprint, occurrence_key, document_event_ids, actor):
    namespace, actor = text(namespace, "Реестр", 80), text(actor, "Автор")
    if not isinstance(document_event_ids, list) or not document_event_ids or len(document_event_ids) > 5000:
        raise ValueError("Выберите документальные операции Ozon.")
    ids = sorted(text(e, "ID документальной операции") for e in document_event_ids)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        snapshot, events, lots, links = _read(conn, start, end, namespace)
        if snapshot is None:
            raise ValueError("Источник Ozon изменился; повторите предпросмотр.")
        source_fingerprint = digest(snapshot)
        previous = links.get(occurrence_key)
        if previous is not None and sorted(previous) != ids:
            raise ValueError("Связь уже подтверждена с другими документами; история не перезаписывается.")
        before = preview_snapshot(snapshot, namespace, events=events, lots=lots, links=links)
        if previous is None and before["fingerprint"] != expected_fingerprint:
            raise ValueError("Источник или документы Ozon изменились; повторите предпросмотр.")
        links[occurrence_key] = ids
        result = preview_snapshot(snapshot, namespace, events=events, lots=lots, links=links)
        candidate = next((r for r in result["rows"] if r["source_occurrence_key"] == occurrence_key), None)
        if candidate is None or not candidate["eligible"]:
            raise ValueError("Связь не прошла сверку: " + "; ".join(candidate["blockers"] if candidate else ["строка отсутствует"]))
        if any(not r["eligible"] and links.get(r["source_occurrence_key"]) for r in result["rows"]):
            raise ValueError("Документальная операция уже использована другой строкой.")
        if previous is None:
            conn.execute("INSERT INTO ozon_normalization_links(period_key,namespace,source_fingerprint,occurrence_key,document_ids_json,actor,created_at) VALUES(?,?,?,?,?,?,?)", (_period(start, end), namespace, source_fingerprint, occurrence_key, canonical(ids), actor, datetime.now(timezone.utc).isoformat()))
        return {"saved": True, "duplicate": previous is not None, "preview": result}
