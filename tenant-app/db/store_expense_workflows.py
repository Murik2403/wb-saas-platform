"""Persistence workflows for recurring and imported tenant-wide expenses."""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import math
import re
import uuid
from calendar import monthrange
from typing import Any

from .core import connect
from .manual_expenses import EXPENSE_ARTICLES

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _preview_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "valid_count": sum(not row["errors"] and not row["duplicate"] for row in rows),
        "invalid_count": sum(bool(row["errors"]) for row in rows),
        "duplicate_count": sum(bool(row["duplicate"]) for row in rows),
    }


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _iso_date(value: Any, field: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def _row_fingerprint(article: str, amount: float, start: str, end: str, note: str) -> str:
    canonical = json.dumps([article, round(amount, 2), start, end, note], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_recurring_store_expense_template(
    *, article: str, amount_rub: float, note: str = "", active: bool = True,
) -> int:
    if article not in EXPENSE_ARTICLES:
        raise ValueError("unsupported expense article")
    if article == "wb_commission":
        raise ValueError("Комиссия WB уже учтена и не может быть регулярным расходом")
    try:
        amount = float(amount_rub)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid recurring expense amount") from exc
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("amount_rub must be finite and non-negative")
    now = _now()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO recurring_store_expense_templates(
                   article,amount_rub,note,active,created_at,updated_at
               ) VALUES (?,?,?,?,?,?)""",
            (article, amount, str(note or "").strip(), int(bool(active)), now, now),
        )
        return int(cur.lastrowid)


def list_recurring_store_expense_templates() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM recurring_store_expense_templates ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def set_recurring_store_expense_template_active(template_id: int, active: bool) -> bool:
    """Pause/resume one recurring template without changing other templates."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE recurring_store_expense_templates SET active=?,updated_at=? WHERE id=?",
            (int(bool(active)), _now(), int(template_id)),
        )
        return cur.rowcount == 1


def generate_recurring_store_expenses(*, year: int, month: int) -> dict[str, Any]:
    try:
        year, month = int(year), int(month)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("year and month must be integers") from exc
    if year < 1 or year > 9999 or month < 1 or month > 12:
        raise ValueError("invalid year or month")
    now = _now()
    period_start = f"{year:04d}-{month:02d}-01"
    period_end = f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"
    created_ids: list[int] = []
    created_count = 0
    already_exists = 0
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        templates = conn.execute(
            "SELECT * FROM recurring_store_expense_templates WHERE active=1 ORDER BY id"
        ).fetchall()
        for template in templates:
            existing = conn.execute(
                """SELECT expense_id,status FROM recurring_store_expense_occurrences
                     WHERE template_id=? AND period_start=?""",
                (int(template["id"]), period_start),
            ).fetchone()
            if existing is not None:
                already_exists += 1
                if existing["expense_id"] is not None:
                    created_ids.append(int(existing["expense_id"]))
                continue
            expense = conn.execute(
                """INSERT INTO manual_store_expenses(
                       article,amount_rub,source,period_start,period_end,note,
                       created_at,updated_at,include_in_management_result
                   ) VALUES (?,?, 'manual', ?,?,?, ?,?,0)""",
                (str(template["article"]), float(template["amount_rub"]), period_start,
                 period_end, str(template["note"] or ""), now, now),
            )
            expense_id = int(expense.lastrowid)
            created_count += 1
            conn.execute(
                """INSERT INTO recurring_store_expense_occurrences(
                       template_id,period_start,expense_id,status,created_at
                   ) VALUES (?,?,?,'active',?)""",
                (int(template["id"]), period_start, expense_id, now),
            )
            created_ids.append(expense_id)
    return {
        "created": created_count,
        "already_exists": already_exists,
        "expense_ids": created_ids,
    }


def _normalize_preview_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, (list, tuple)):
        raise ValueError("rows must be a list")
    if len(rows) > 500:
        raise ValueError("preview row limit exceeded")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    seen_fingerprints: set[str] = set()
    with connect() as conn:
        existing_rows = conn.execute(
            "SELECT article,amount_rub,period_start,period_end,note,row_fingerprint FROM manual_store_expenses"
        ).fetchall()
    existing = {
        (str(r["article"]), float(r["amount_rub"]), str(r["period_start"]),
         str(r["period_end"]), str(r["note"] or ""))
        for r in existing_rows
    }
    existing_import_fingerprints = {str(r["row_fingerprint"]) for r in existing_rows if r["row_fingerprint"]}
    for index, raw in enumerate(rows, start=2):
        raw = raw if isinstance(raw, dict) else {}
        try:
            line_number = int(raw.get("line_number", index))
        except (TypeError, ValueError, OverflowError):
            line_number = index
        article_value = raw.get("article")
        article = str(article_value).strip() if article_value is not None else ""
        amount_raw = raw.get("amount_rub")
        try:
            amount = float(amount_raw)
            amount_valid = math.isfinite(amount) and amount >= 0
        except (TypeError, ValueError, OverflowError):
            amount, amount_valid = None, False
        start_raw, end_raw = raw.get("period_start"), raw.get("period_end")
        try:
            start = _iso_date(start_raw, "period_start")
            end = _iso_date(end_raw, "period_end")
            dates_valid = end >= start
        except ValueError:
            start = str(start_raw or "")
            end = str(end_raw or "")
            dates_valid = False
        errors_value = raw.get("errors", [])
        errors = [str(error) for error in errors_value] if isinstance(errors_value, (list, tuple)) else [str(errors_value)]
        if article not in EXPENSE_ARTICLES:
            errors.append("unsupported expense article")
        if not amount_valid:
            errors.append("amount_rub must be finite and non-negative")
        if not dates_valid:
            errors.append("invalid expense period")
        if article == "wb_commission":
            errors.append("Комиссия WB уже учтена и не может быть импортирована в результат")
        note = str(raw.get("note") or "").strip()
        duplicate = bool(raw.get("duplicate", False))
        signature = (article, amount, start, end, note)
        fingerprint = _row_fingerprint(article, amount, start, end, note) if amount_valid and dates_valid else ""
        if (signature in seen or (amount_valid and dates_valid and signature in existing)
                or bool(fingerprint and (fingerprint in seen_fingerprints or fingerprint in existing_import_fingerprints))):
            duplicate = True
        seen.add(signature)
        if fingerprint:
            seen_fingerprints.add(fingerprint)
        normalized.append({
            "line_number": line_number,
            "article": article,
            "amount_rub": amount,
            "period_start": start,
            "period_end": end,
            "note": note,
            "errors": list(dict.fromkeys(errors)),
            "duplicate": duplicate,
            "row_fingerprint": fingerprint,
        })
    return normalized


def save_store_expense_import_preview(*, content_sha256: str, rows: Any) -> dict[str, Any]:
    digest = str(content_sha256 or "").lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
    normalized = _normalize_preview_rows(rows)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id,status,rows_json,result_json FROM store_expense_import_batches WHERE content_sha256=?",
            (digest,),
        ).fetchone()
        if existing is not None:
            saved_rows = json.loads(existing["rows_json"])
            already_imported = str(existing["status"]) == "confirmed"
            return {
                "batch_id": str(existing["id"]),
                # A pending preview is the same batch, but still needs to be
                # confirmable when the user re-uploads the file.
                "already_imported": already_imported,
                "rows": saved_rows,
                "result": json.loads(existing["result_json"]) if existing["result_json"] else None,
                **_preview_counts(saved_rows),
            }
        batch_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO store_expense_import_batches(
                   id,content_sha256,status,rows_json,created_at
               ) VALUES (?,?,'preview',?,?)""",
            (batch_id, digest, json.dumps(normalized, ensure_ascii=False, separators=(",", ":")), _now()),
        )
    return {
        "batch_id": batch_id, "already_imported": False, "rows": normalized, "result": None,
        **_preview_counts(normalized),
    }


def confirm_store_expense_import(batch_id: str) -> dict[str, Any]:
    now = _now()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = conn.execute(
            "SELECT status,rows_json,result_json FROM store_expense_import_batches WHERE id=?",
            (str(batch_id),),
        ).fetchone()
        if batch is None:
            raise ValueError("store expense import batch not found")
        if str(batch["status"]) == "confirmed":
            if not batch["result_json"]:
                raise RuntimeError("confirmed import batch has no saved result")
            result = json.loads(batch["result_json"])
            result["already_confirmed"] = True
            return result
        rows = json.loads(batch["rows_json"])
        expense_ids: list[int] = []
        for row in rows:
            if row.get("errors") or row.get("duplicate"):
                continue
            article = str(row.get("article") or "")
            if article not in EXPENSE_ARTICLES or article == "wb_commission":
                continue
            amount = float(row["amount_rub"])
            if not math.isfinite(amount) or amount < 0:
                continue
            start = _iso_date(row.get("period_start"), "period_start")
            end = _iso_date(row.get("period_end"), "period_end")
            if end < start:
                continue
            fingerprint = str(row.get("row_fingerprint") or _row_fingerprint(
                article, amount, start, end, str(row.get("note") or "").strip()
            ))
            if conn.execute(
                "SELECT 1 FROM manual_store_expenses WHERE row_fingerprint=?", (fingerprint,)
            ).fetchone() is not None:
                continue
            cur = conn.execute(
                """INSERT INTO manual_store_expenses(
                       article,amount_rub,source,period_start,period_end,note,
                       created_at,updated_at,include_in_management_result,row_fingerprint
                   ) VALUES (?,?, 'import', ?,?,?, ?,?,0,?)""",
                (article, amount, start, end, str(row.get("note") or ""), now, now, fingerprint),
            )
            expense_ids.append(int(cur.lastrowid))
        result = {
            "batch_id": str(batch_id), "created_count": len(expense_ids),
            "expense_ids": expense_ids, "already_confirmed": False,
        }
        conn.execute(
            """UPDATE store_expense_import_batches
                  SET status='confirmed',result_json=?,confirmed_at=? WHERE id=?""",
            (json.dumps(result, separators=(",", ":")), now, str(batch_id)),
        )
    return result
