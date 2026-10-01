"""Read-only fee extraction from Ozon by-day v1; never creates ledger events.

Contract inspected at https://docs.ozon.ru/api/seller/#operation/GetFinanceAccrualByDay
on 2026-10-01. No posting formula, movement, correction or operation uniqueness
is inferred. Source keys identify occurrences in this immutable preview only.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json


def _money(value):
    if not isinstance(value, dict) or value.get("currency") != "RUB" or not isinstance(value.get("amount"), str):
        raise ValueError("Требуется строковая сумма в RUB; конвертация не выполняется.")
    try:
        amount = Decimal(value["amount"])
    except InvalidOperation:
        raise ValueError("Некорректная сумма сбора.") from None
    if not amount.is_finite() or abs(amount) >= Decimal("1e18") or amount != amount.quantize(Decimal("0.01")):
        raise ValueError("Сумма сбора должна быть конечной с точностью до копейки.")
    return amount


def _display_date(value):
    if not isinstance(value, str):
        return None
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _display_label(entry, type_id):
    for field in ("description", "name"):
        value = entry.get(field)
        if isinstance(value, str) and value:
            return value
    return str(type_id)


def fee_preview(rows, types):
    encoded = json.dumps({"rows": rows, "types": types}, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    version = hashlib.sha256(encoded.encode()).hexdigest()
    known = {r.get("id"): r for r in types if isinstance(r, dict) and type(r.get("id")) is int}
    results = []
    total = Decimal(0)
    for index, row in enumerate(rows):
        result = {"source_occurrence_key": f"ozon:fee-preview:v1:{version}:{index}", "source_row_index": index,
                  "reconciled": False, "components": [], "component_sum_rub": None}
        try:
            if not isinstance(row, dict):
                raise ValueError("Не распознана строка начисления.")
            # Rejected rows still reach the UI; never copy raw containers into
            # display metadata. Validation below continues using the raw row.
            result.update({"accrual_id": row.get("accrual_id") if type(row.get("accrual_id")) is int and row["accrual_id"] >= 0 else None,
                           "date": _display_date(row.get("date")),
                           "category": row.get("accrued_category") if isinstance(row.get("accrued_category"), str) else None})
            if type(row.get("accrual_id")) is not int or row["accrual_id"] < 0:
                raise ValueError("Не распознан идентификатор начисления.")
            if date.fromisoformat(row["date"]).isoformat() != row["date"]:
                raise ValueError("Дата начисления должна иметь формат ГГГГ-ММ-ДД.")
            category = row.get("accrued_category")
            branches = {"NON_ITEM": "non_item_fee", "ITEM": "item_fees", "CONTAINER_FEES": "container_fees"}
            if category not in branches:
                raise ValueError("Категория не поддерживается preview сборов; продажи и возвраты требуют отдельного контракта.")
            active = branches[category]
            if any(row.get(k) is not None for k in {"posting", *branches.values()} - {active}):
                raise ValueError("Неоднозначный состав начисления: заполнены другие категории.")
            expected = _money(row.get("total_amount"))
            entries = []
            if category == "NON_ITEM":
                entries = [(row[active], active, None)]
            else:
                groups = row[active]["fees"]
                if not isinstance(groups, list) or not groups:
                    raise ValueError("Пустой или некорректный список сборов.")
                for gi, group in enumerate(groups):
                    path = f"{active}.fees[{gi}]"
                    if category == "CONTAINER_FEES":
                        entries.append((group, path, None))
                    else:
                        sku = group["sku"]
                        if type(sku) is not int or sku <= 0 or not isinstance(group["fees"], list) or not group["fees"]:
                            raise ValueError("Не распознан SKU или состав сборов товара.")
                        entries.extend((fee, f"{path}.fees[{fi}]", str(sku)) for fi, fee in enumerate(group["fees"]))
            components = []
            component_sum = Decimal(0)
            for fee, path, sku in entries:
                if not isinstance(fee, dict) or set(fee) != {"type_id", "accrued"}:
                    raise ValueError("Не распознан состав сбора; новые поля требуют проверки контракта.")
                type_id = fee["type_id"]
                if type(type_id) is not int or type_id not in known or sum(1 for r in types if isinstance(r, dict) and r.get("id") == type_id) != 1:
                    raise ValueError("Тип сбора отсутствует в загруженном справочнике Ozon.")
                amount = _money(fee["accrued"])
                component_sum += amount
                components.append({"source_path": path, "type_id": type_id, "sku": sku, "amount_rub": str(amount),
                                   "name": _display_label(known[type_id], type_id)})
            if component_sum != expected:
                raise ValueError("Сумма компонентов сборов не совпадает с итогом Ozon; строка исключена из preview.")
            result.update({"reconciled": True, "components": components, "component_sum_rub": str(component_sum)})
            total += expected
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            result["reason"] = str(exc) if isinstance(exc, ValueError) else "Не распознана структура начисления Ozon."
        results.append(result)
    return {"contract": "ozon-by-day-v1-fees-preview-2026-10-01", "source_version": version, "rows": results,
            "reconciled_total_rub": str(total) if any(r["reconciled"] for r in results) else None,
            "preview_all_rows_reconciled": bool(results) and all(r["reconciled"] for r in results),
            "financial_normalization_verified": False, "marketplace_source_complete": False, "ledger_confirmation_allowed": False}
