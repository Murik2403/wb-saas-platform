"""Strict parser for tenant-wide expense CSV uploads."""
from __future__ import annotations

import csv
from datetime import date
from io import StringIO
import math
from typing import Any

MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 500
REQUIRED_HEADERS = {"article", "amount_rub", "period_start", "period_end"}
OPTIONAL_HEADERS = {"note"}
EXPENSE_ARTICLES = {
    "purchase_production", "wb_commission", "logistics_returns", "storage",
    "acceptance", "advertising", "packaging_labor", "defects_losses", "taxes",
}


class ExpenseCsvError(ValueError):
    """The uploaded file cannot be interpreted as a supported expense CSV."""


def _amount(value: str) -> float:
    raw = value.strip().replace("\u00a0", "").replace(" ", "")
    if raw.count(",") == 1 and "." not in raw:
        raw = raw.replace(",", ".")
    result = float(raw)
    if not math.isfinite(result) or result < 0:
        raise ValueError("Сумма должна быть конечным неотрицательным числом")
    return round(result, 2)


def _date(value: str, field: str) -> str:
    parsed = date.fromisoformat(value.strip())
    return parsed.isoformat()


def parse_store_expense_csv(content: bytes) -> list[dict[str, Any]]:
    """Parse CSV rows into a normalized preview; no database writes occur here."""
    if len(content) > MAX_IMPORT_BYTES:
        raise ExpenseCsvError("CSV не должен превышать 5 МиБ")
    try:
        text = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ExpenseCsvError("Файл должен быть сохранён в UTF-8") from exc
    if not text.strip():
        raise ExpenseCsvError("CSV-файл пуст")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        delimiter = dialect.delimiter
    except csv.Error:
        first_line = sample.splitlines()[0]
        delimiter = ";" if first_line.count(";") > first_line.count(",") else ","

    reader = csv.DictReader(StringIO(text, newline=""), delimiter=delimiter)
    headers = [str(header or "").strip() for header in (reader.fieldnames or [])]
    if not headers:
        raise ExpenseCsvError("Не найдена строка заголовков")
    missing = sorted(REQUIRED_HEADERS - set(headers))
    unknown = sorted(set(headers) - REQUIRED_HEADERS - OPTIONAL_HEADERS)
    if missing or unknown or len(headers) != len(set(headers)):
        details = []
        if missing:
            details.append("нет колонок: " + ", ".join(missing))
        if unknown:
            details.append("неподдерживаемые колонки: " + ", ".join(unknown))
        if len(headers) != len(set(headers)):
            details.append("названия колонок повторяются")
        raise ExpenseCsvError("Некорректные заголовки CSV: " + "; ".join(details))

    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    data_row_count = 0
    for line_number, source_row in enumerate(reader, start=2):
        if not source_row or not any(str(value or "").strip() for value in source_row.values()):
            continue
        # DictReader stores surplus cells under a None key and missing cells
        # as None values. Do not silently discard malformed row data.
        if None in source_row or any(value is None for value in source_row.values()):
            raise ExpenseCsvError(f"Строка {line_number}: число ячеек не совпадает с заголовками")
        data_row_count += 1
        if data_row_count > MAX_IMPORT_ROWS:
            raise ExpenseCsvError("В одном файле допускается не более 500 строк данных")
        errors: list[str] = []
        article = str(source_row.get("article") or "").strip()
        if article not in EXPENSE_ARTICLES:
            errors.append("Неизвестная статья расхода")
        elif article == "wb_commission":
            errors.append("Комиссия WB уже учтена в выплате и не импортируется как внешний расход")
        try:
            amount = _amount(str(source_row.get("amount_rub") or ""))
        except (ValueError, TypeError):
            amount = None
            errors.append("Некорректная сумма в ₽")
        try:
            start = _date(str(source_row.get("period_start") or ""), "period_start")
        except (ValueError, TypeError):
            start = None
            errors.append("Дата начала должна быть в формате ГГГГ-ММ-ДД")
        try:
            end = _date(str(source_row.get("period_end") or ""), "period_end")
        except (ValueError, TypeError):
            end = None
            errors.append("Дата окончания должна быть в формате ГГГГ-ММ-ДД")
        if start and end and end < start:
            errors.append("Дата окончания раньше даты начала")
        note = str(source_row.get("note") or "").strip()
        if len(note) > 1000:
            errors.append("Примечание не должно превышать 1000 символов")

        duplicate = False
        if not errors:
            fingerprint = (article, amount, start, end, note)
            duplicate = fingerprint in seen
            seen.add(fingerprint)
        rows.append({
            "line_number": line_number,
            "article": article,
            "amount_rub": amount,
            "period_start": start,
            "period_end": end,
            "note": note,
            "errors": errors,
            "duplicate": duplicate,
        })
    if not rows:
        raise ExpenseCsvError("В CSV нет строк расходов")
    return rows
