"""Read-only observation of /v1/finance/accrual/postings fields.

Source quantity is not a movement, and accrued values have no authoritative
posting aggregate here. No joins, category interpretation or ledger writes.
"""
import hashlib
import json
from datetime import date
from .ozon_fee_preview import _money


def posting_evidence(snapshot):
    source = snapshot.get("finance_postings")
    loaded = isinstance(source, list)
    encoded = json.dumps(source, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    version = hashlib.sha256(encoded.encode()).hexdigest()
    rows = []
    count = 0
    for gi, group in enumerate(source if loaded else []):
        for ai, item in enumerate(group.get("accruals", [])):
            count += 1
            if len(rows) >= 100:
                continue
            quantity = item.get("quantity")
            quantity_valid = type(quantity) is int and -(2**31) <= quantity < 2**31
            accrual_date = item.get("accrual_date")
            try:
                date_valid = isinstance(accrual_date, str) and date.fromisoformat(accrual_date).isoformat() == accrual_date
            except ValueError:
                date_valid = False
            result = {"source_occurrence_key":f"ozon:posting-evidence:v1:{version}:{gi}:{ai}",
                      "source_path":f"finance_postings[{gi}].accruals[{ai}]",
                      "posting_number":group.get("posting_number") if isinstance(group.get("posting_number"), str) else None,
                      "accrual_date":accrual_date if date_valid else None,
                      "sku_text":str(item["sku"]) if type(item.get("sku")) is int and 0 < item["sku"] < 2**63 else None,
                      "type_id_text":str(item["type_id"]) if type(item.get("type_id")) is int and -(2**31) <= item["type_id"] < 2**31 else None,
                      "quantity_text":str(quantity) if quantity_valid else None,
                      "accrued_rub":None, "seller_price_rub":None}
            reasons = [] if quantity_valid else ["Количество не указано в источнике." if quantity is None else "Количество не распознано как целое число источника."]
            if not date_valid:
                reasons.append("Дата начисления не распознана как YYYY-MM-DD.")
            for field in ("accrued", "seller_price"):
                if field == "seller_price" and item.get(field) is None:
                    continue
                try:
                    result[field + "_rub"] = str(_money(item.get(field)))
                except ValueError:
                    reasons.append("Не распознана сумма в RUB: " + field + ".")
            if reasons:
                result["reason"] = " ".join(reasons)
            rows.append(result)
    return {"loaded":loaded, "source_version":version, "rows":rows, "source_row_count":count,
            "display_truncated":count > len(rows), "scope":snapshot.get("finance_postings_scope"),
            "movement_verified":False, "financial_normalization_verified":False,
            "return_linkage_verified":False, "ledger_confirmation_allowed":False}
