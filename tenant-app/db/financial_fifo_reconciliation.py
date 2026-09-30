"""Read-only reconciliation between WB financial sale/return rows and FIFO events.

This module deliberately does not adjust the financial report or infer a cost
when an operation cannot be uniquely tied to fully allocated, trusted layers.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

import pandas as pd

from .core import connect


# Only source layers with a recorded production or procurement cost are accepted
# as exact by this diagnostic. Opening/reconciliation/synthetic layers need their
# provenance reviewed before they can support a financial FIFO scenario.
_TRUSTED_LAYER_SOURCES = {"production", "production_wip", "procurement_product"}
_PLACEHOLDER_LAYER_SOURCES = {
    "synthetic_sale", "return_unmatched", "opening_wb", "opening_ready",
    "opening_inbound", "reconstruction_cost_pending",
}

_COLUMNS = [
    "rrd_id", "operation_date", "sale_date", "nm_id", "supplier_article",
    "operation_name", "doc_type_name", "srid", "event_type", "units",
    "baseline_cogs_rub", "status", "reason", "matched_event_ids",
    "matched_units", "eligible_fifo_cogs_rub", "observed_fifo_cost_rub",
    "layer_sources",
]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_srid(raw_json: Any) -> str:
    try:
        payload = json.loads(_text(raw_json) or "{}")
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return _text(payload.get("srid") or payload.get("SRID"))


def _event_type(row: dict[str, Any]) -> str:
    operation = f"{_text(row.get('doc_type_name'))} {_text(row.get('operation_name'))}".casefold()
    if "возврат" in operation:
        return "return"
    if "продаж" in operation:
        return "sale"
    return ""


def _source_summary(values: Iterable[Any]) -> list[str]:
    return sorted({_text(value) for value in values if _text(value)})


def _chunks(values: list[Any], size: int = 400) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _reconcile(
    financial_rows: list[dict[str, Any]],
    fifo_events: list[dict[str, Any]],
    allocation_rows: list[dict[str, Any]],
    baseline_rates: dict[int, float],
) -> list[dict[str, Any]]:
    allocations: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for allocation in allocation_rows:
        allocations[int(allocation["event_id"])].append(allocation)
    events_by_key: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    events_by_id: dict[int, dict[str, Any]] = {}
    for event in fifo_events:
        normalized = dict(event)
        normalized["id"] = int(event["id"])
        normalized["nm_id"] = int(event.get("nm_id") or 0)
        normalized["units"] = max(0, int(event.get("units") or 0))
        normalized["fifo_cost_rub"] = max(0.0, float(event.get("fifo_cost_rub") or 0))
        normalized["source_srid"] = _json_srid(event.get("sales_raw_json"))
        events_by_id[normalized["id"]] = normalized
        srid = _text(event.get("srid"))
        if srid:
            events_by_key[(normalized["nm_id"], srid, _text(event.get("event_type")))].append(normalized)

    financial_keys: dict[tuple[int, str, str], int] = defaultdict(int)
    prepared: list[dict[str, Any]] = []
    for source in financial_rows:
        row = dict(source)
        kind = _event_type(row)
        quantity = abs(int(row.get("quantity") or 0))
        srid = _json_srid(row.get("raw_json"))
        nm_id = int(row.get("nm_id") or 0)
        if kind and srid and quantity:
            financial_keys[(nm_id, srid, kind)] += 1
        row.update({"_kind": kind, "_units": quantity, "_srid": srid, "_nm_id": nm_id})
        prepared.append(row)

    output: list[dict[str, Any]] = []
    for row in prepared:
        kind = row["_kind"]
        units = row["_units"]
        nm_id = row["_nm_id"]
        srid = row["_srid"]
        sign = -1 if kind == "return" else 1
        baseline = round(float(baseline_rates.get(nm_id, 0.0)) * units * sign, 2) if kind else 0.0
        result = {
            "rrd_id": int(row.get("rrd_id") or 0),
            "operation_date": _text(row.get("operation_date")),
            "sale_date": _text(row.get("sale_date")),
            "nm_id": nm_id,
            "supplier_article": _text(row.get("supplier_article")),
            "operation_name": _text(row.get("operation_name")),
            "doc_type_name": _text(row.get("doc_type_name")),
            "srid": srid,
            "event_type": kind,
            "units": units,
            "baseline_cogs_rub": baseline,
            "status": "unmatched",
            "reason": "",
            "matched_event_ids": [],
            "matched_units": 0,
            "eligible_fifo_cogs_rub": None,
            "observed_fifo_cost_rub": None,
            "layer_sources": [],
        }
        if not kind:
            result.update(status="excluded", reason="not_sale_or_return_operation")
            output.append(result)
            continue
        if units <= 0:
            result.update(status="ambiguous", reason="financial_quantity_zero")
            output.append(result)
            continue
        if not srid:
            result.update(status="unmatched", reason="financial_srid_missing")
            output.append(result)
            continue
        key = (nm_id, srid, kind)
        if financial_keys[key] != 1:
            result.update(status="ambiguous", reason="duplicate_financial_rows_for_srid")
            output.append(result)
            continue
        matches = events_by_key.get(key, [])
        if not matches:
            result.update(status="unmatched", reason="fifo_event_missing")
            output.append(result)
            continue
        if sum(event["units"] for event in matches) != units:
            result.update(
                status="ambiguous", reason="fifo_financial_unit_count_mismatch",
                matched_event_ids=[event["id"] for event in matches],
                matched_units=sum(event["units"] for event in matches),
            )
            output.append(result)
            continue

        result["matched_event_ids"] = [event["id"] for event in matches]
        result["matched_units"] = units
        observed_total = 0.0
        all_exact = True
        reasons: list[str] = []
        sources: list[str] = []
        for event in matches:
            if not _text(event.get("source_srid")):
                all_exact = False
                reasons.append("source_sales_srid_missing")
                continue
            if _text(event.get("source_srid")) != srid:
                all_exact = False
                reasons.append("source_sales_srid_mismatch")
                continue
            if _text(event.get("status")) == "error":
                all_exact = False
                reasons.append("fifo_event_error")
                continue
            if _text(event.get("status")) == "baseline":
                all_exact = False
                reasons.append("fifo_event_is_baseline")
                continue
            if _text(event.get("status")) != "applied":
                all_exact = False
                reasons.append("fifo_event_not_applied")
                continue
            if kind == "return":
                original = events_by_id.get(int(event.get("matched_sale_event_id") or 0))
                if not original or _text(original.get("event_type")) != "sale" or int(original.get("nm_id") or 0) != nm_id or _text(original.get("srid")) != srid:
                    all_exact = False
                    reasons.append("return_original_sale_not_verified")
                    continue
                if not _text(original.get("source_srid")):
                    all_exact = False
                    reasons.append("return_original_sales_srid_missing")
                    continue
                if _text(original.get("source_srid")) != srid:
                    all_exact = False
                    reasons.append("return_original_sales_srid_mismatch")
                    continue
            event_allocations = allocations.get(event["id"], [])
            allocated_units = sum(max(0, int(item.get("units") or 0)) for item in event_allocations)
            if allocated_units != event["units"] or not event_allocations:
                all_exact = False
                reasons.append("fifo_layer_allocations_incomplete")
                continue
            event_sources = _source_summary(item.get("source_type") for item in event_allocations)
            sources.extend(event_sources)
            if any(source in _PLACEHOLDER_LAYER_SOURCES or source not in _TRUSTED_LAYER_SOURCES for source in event_sources):
                all_exact = False
                if any(source in _PLACEHOLDER_LAYER_SOURCES for source in event_sources):
                    reasons.append("placeholder_or_opening_layer")
                else:
                    reasons.append("unverified_fifo_layer_source")
                continue
            # Verify event cost against the layers allocated to that event.
            layer_cost = sum(float(item.get("amount_rub") or 0) for item in event_allocations)
            if abs(layer_cost - event["fifo_cost_rub"]) > 0.02:
                all_exact = False
                reasons.append("event_cost_differs_from_allocations")
                continue
            observed_total += event["fifo_cost_rub"]

        result["layer_sources"] = _source_summary(sources)
        if all_exact:
            result.update(
                status="exact", reason="", eligible_fifo_cogs_rub=round(observed_total * sign, 2),
                observed_fifo_cost_rub=round(observed_total, 2),
            )
        else:
            unique_reasons = sorted(set(reasons))
            if unique_reasons == ["fifo_event_is_baseline"]:
                category = "baseline"
            elif "placeholder_or_opening_layer" in unique_reasons:
                category = "synthetic"
            elif any(reason.endswith("_srid_missing") for reason in unique_reasons) or "return_original_sale_not_verified" in unique_reasons:
                category = "unmatched"
            else:
                category = "ambiguous"
            result.update(
                status=category, reason=";".join(unique_reasons) or "fifo_cost_not_verified",
                observed_fifo_cost_rub=round(sum(event["fifo_cost_rub"] for event in matches), 2),
            )
        output.append(result)
    return output


def read_financial_fifo_reconciliation(date_from: str, date_to: str,*,allow_local_fifo:bool=True) -> pd.DataFrame:
    """Return read-only financial-operation to FIFO diagnostics for an operation-date range."""
    start = date.fromisoformat(str(date_from))
    end_exclusive = date.fromisoformat(str(date_to)) + timedelta(days=1)
    if end_exclusive <= start:
        return pd.DataFrame(columns=_COLUMNS)
    with connect() as conn:
        conn.execute('BEGIN')
        finance = conn.execute(
            """SELECT *
                 FROM financial_report
                WHERE operation_date >= ? AND operation_date < ?
                ORDER BY operation_date,rrd_id""",
            (start.isoformat(), end_exclusive.isoformat()),
        ).fetchall()
        financial_rows = [dict(row) for row in finance]
        relevant_srids: dict[int, set[str]] = defaultdict(set)
        relevant_nm_ids: set[int] = set()
        for row in financial_rows:
            if not _event_type(row):
                continue
            nm_id = int(row.get("nm_id") or 0)
            relevant_nm_ids.add(nm_id)
            srid = _json_srid(row.get("raw_json"))
            if srid:
                relevant_srids[nm_id].add(srid)
        fifo_events: list[dict[str, Any]] = []
        allocation_rows: list[dict[str, Any]] = []
        baseline_rates: dict[int, float] = {}
        if relevant_nm_ids:
            # Fetch only event keys represented by this financial window. A
            # whole-history scan per SKU grows with a tenant's lifetime and
            # can exceed SQLite's bound-parameter limit for allocations.
            for nm_id, srids in (relevant_srids.items() if allow_local_fifo else ()):
                for srid_chunk in _chunks(sorted(srids)):
                    srid_placeholders = ",".join("?" for _ in srid_chunk)
                    fifo_events.extend(dict(row) for row in conn.execute(
                        f"""SELECT e.*,s.raw_json AS sales_raw_json
                              FROM sales_fifo_events e
                              LEFT JOIN sales s ON s.id=e.sale_record_id
                             WHERE e.nm_id=? AND e.srid IN ({srid_placeholders})
                               AND e.event_type IN ('sale','return')
                             ORDER BY e.id""",
                        [nm_id, *srid_chunk],
                    ).fetchall())
            event_ids = [int(event["id"]) for event in fifo_events]
            for event_chunk in _chunks(event_ids):
                event_placeholders = ",".join("?" for _ in event_chunk)
                allocation_rows.extend(dict(row) for row in conn.execute(
                    f"""SELECT e.id AS event_id,a.units,a.amount_rub,l.source_type
                           FROM sales_fifo_events e
                           JOIN finished_goods_fifo_allocations a ON a.movement_id=e.movement_id
                           JOIN finished_goods_cost_layers l ON l.id=a.layer_id
                          WHERE e.id IN ({event_placeholders})
                            AND a.status='applied' AND a.reversed_at IS NULL""",
                    event_chunk,
                ).fetchall())
            nm_id_list = sorted(relevant_nm_ids)
            for nm_chunk in _chunks(nm_id_list):
                placeholders = ",".join("?" for _ in nm_chunk)
                costs = conn.execute(
                    f"SELECT nm_id,cost_per_wb_unit FROM costs WHERE nm_id IN ({placeholders})",
                    nm_chunk,
                ).fetchall()
                baseline_rates.update({int(row["nm_id"]): max(0.0, float(row["cost_per_wb_unit"] or 0)) for row in costs})
        rows = _reconcile(financial_rows, fifo_events, allocation_rows, baseline_rates)
        from shared_warehouse_finance import apply_projection
        rows = apply_projection(conn,financial_rows,rows)
    return pd.DataFrame(rows, columns=_COLUMNS)


def summarize_financial_fifo_reconciliation(rows: pd.DataFrame) -> dict[str, Any]:
    """Return a compact, non-accounting summary for the finance dashboard."""
    if rows.empty:
        return {
            "sale_return_rows": 0,
            "exact_rows": 0,
            "sale_return_units": 0,
            "exact_units": 0,
            "exact_unit_coverage_percent": 0.0,
            "exact_fifo_cogs_rub": 0.0,
            "status_counts": {},
        }
    eligible = rows[rows["event_type"].isin(("sale", "return"))]
    counts = {str(key): int(value) for key, value in eligible["status"].value_counts().items()}
    exact = eligible[eligible["status"] == "exact"]
    sale_return_units = int(pd.to_numeric(eligible["units"], errors="coerce").fillna(0).abs().sum())
    exact_units = int(pd.to_numeric(exact["units"], errors="coerce").fillna(0).abs().sum())
    exact_cost = pd.to_numeric(exact["eligible_fifo_cogs_rub"], errors="coerce").sum()
    return {
        "sale_return_rows": int(len(eligible)),
        "exact_rows": int(len(exact)),
        "sale_return_units": sale_return_units,
        "exact_units": exact_units,
        "exact_unit_coverage_percent": round(exact_units / sale_return_units * 100, 2) if sale_return_units else 0.0,
        "exact_fifo_cogs_rub": round(float(exact_cost or 0), 2),
        "status_counts": counts,
    }
