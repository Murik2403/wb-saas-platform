"""Project audited operation-date FIFO rows into a hybrid COGS view."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd


_DETAIL_COLUMNS = [
    "rrd_id", "nm_id", "event_type", "units", "cogs_rub", "cogs_source", "reason",
]
_GROUP_COLUMNS = [
    "nm_id", "cogs_rub", "exact_fifo_cogs_rub", "current_cost_estimate_rub",
    "exact_units", "estimated_units", "insufficient_units", "sale_return_units",
    "exact_rows", "estimated_rows", "insufficient_rows", "exact_unit_coverage_percent",
    "known_unit_coverage_percent", "cogs_quality", "cogs_complete",
    "manual_source_cogs_rub", "manual_source_units",
]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _totals(rows: pd.DataFrame, *, include_nm_id: bool) -> dict[str, Any]:
    # Filters below need only these columns, never the per-row provenance payload.
    rows = rows[["cogs_source", "units", "cogs_rub"]]
    events = rows[rows["cogs_source"].isin(("exact_fifo", "current_cost_estimate", "manual_source_fifo", "insufficient"))]
    exact = events[events["cogs_source"] == "exact_fifo"]
    estimated = events[events["cogs_source"].isin(("current_cost_estimate", "manual_source_fifo"))]
    manual = events[events['cogs_source']=='manual_source_fifo']
    insufficient = events[events["cogs_source"] == "insufficient"]
    total_units = int(events["units"].sum())
    exact_units = int(exact["units"].sum())
    estimated_units = int(estimated["units"].sum())
    insufficient_units = int(insufficient["units"].sum())
    if total_units == 0:
        quality = "no_sales"
    elif insufficient_units:
        quality = "insufficient"
    elif exact_units == total_units:
        quality = "exact"
    elif exact_units and estimated_units:
        quality = "mixed"
    else:
        quality = "estimated"
    result: dict[str, Any] = {
        "cogs_rub": round(float(events["cogs_rub"].sum()), 2),
        "exact_fifo_cogs_rub": round(float(exact["cogs_rub"].sum()), 2),
        "current_cost_estimate_rub": round(float(events[events['cogs_source']=='current_cost_estimate']['cogs_rub'].sum()), 2),
        "manual_source_cogs_rub": round(float(manual['cogs_rub'].sum()), 2),
        "manual_source_units": int(manual['units'].sum()),
        "exact_units": exact_units,
        "estimated_units": estimated_units,
        "insufficient_units": insufficient_units,
        "sale_return_units": total_units,
        "exact_rows": int(len(exact)),
        "estimated_rows": int(len(estimated)),
        "insufficient_rows": int(len(insufficient)),
        "exact_unit_coverage_percent": round(exact_units / total_units * 100, 2) if total_units else 0.0,
        "known_unit_coverage_percent": round((exact_units + estimated_units) / total_units * 100, 2) if total_units else 0.0,
        "cogs_quality": quality,
        "cogs_complete": bool(total_units > 0 and insufficient_units == 0 and manual.empty),
    }
    return result


def project_historical_cogs(reconciliation: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Return per-financial-row COGS, per-SKU totals, and store totals.

    ``eligible_fifo_cogs_rub`` and ``baseline_cogs_rub`` are already signed by
    the reconciliation layer. Never apply an event sign a second time. A bad or
    duplicated source id fails closed for every affected sale/return row.
    """
    if reconciliation is None or reconciliation.empty:
        return pd.DataFrame(columns=_DETAIL_COLUMNS), pd.DataFrame(columns=_GROUP_COLUMNS), {
            **_totals(pd.DataFrame(columns=_DETAIL_COLUMNS), include_nm_id=False),
        }

    # These inputs are read-only. A Series per row (iterrows) costs more than
    # the financial calculation itself for production-sized reporting windows.
    # Resolve optional columns once and iterate their scalar values directly.
    source = reconciliation
    ids = pd.to_numeric(source.get("rrd_id", pd.Series(index=source.index, dtype=float)), errors="coerce")
    id_counts = ids.value_counts(dropna=False).to_dict()
    column_positions = {name: index for index, name in enumerate(source.columns)}
    positions = [column_positions.get(name) for name in (
        "event_type", "nm_id", "units", "status", "reason",
        "eligible_fifo_cogs_rub", "baseline_cogs_rub",
    )]
    details: list[tuple[Any, ...]] = []
    for source_id, row in zip(ids.to_numpy(), source.itertuples(index=False, name=None)):
        kind, raw_nm, raw_quantity, raw_status, raw_reason, raw_exact, raw_baseline = (
            row[position] if position is not None else None for position in positions
        )
        event_type = str(kind or "").strip().casefold()
        try:
            nm_id = int(float(raw_nm or 0))
        except (TypeError, ValueError, OverflowError):
            nm_id = 0
        raw_units = _number(raw_quantity)
        units = max(0, int(abs(raw_units or 0))) if event_type in ("sale", "return") else 0
        valid_id = pd.notna(source_id) and int(source_id) > 0 and int(id_counts.get(source_id, 0)) == 1
        status = str(raw_status or "").strip().casefold()
        reason = str(raw_reason or "")
        cogs = 0.0
        if event_type not in ("sale", "return") or status == "excluded":
            cogs_source = "excluded"
        elif not valid_id:
            cogs_source = "insufficient"
            reason = "financial_rrd_id_missing_or_duplicate"
        elif units <= 0:
            cogs_source = "insufficient"
            reason = reason or "financial_quantity_zero"
        elif status in {"exact","manual_source"}:
            exact_value = _number(raw_exact)
            expected_sign = 1 if event_type == "sale" else -1
            sign_is_valid = exact_value is not None and (exact_value >= 0 if event_type == "sale" else exact_value <= 0)
            if sign_is_valid:
                cogs, cogs_source = exact_value, "manual_source_fifo" if status=='manual_source' else "exact_fifo"
            else:
                cogs_source = "insufficient"
                reason = "exact_fifo_cost_missing_or_wrong_sign"
        else:
            baseline = _number(raw_baseline)
            expected_sign = 1 if event_type == "sale" else -1
            if baseline is not None and baseline * expected_sign > 0:
                cogs, cogs_source = baseline, "current_cost_estimate"
            else:
                cogs_source = "insufficient"
                reason = reason or "current_cost_baseline_missing_or_zero"
        details.append((
            int(source_id) if pd.notna(source_id) else None,
            nm_id, event_type, units, float(cogs), cogs_source, reason,
        ))

    detail = pd.DataFrame.from_records(details, columns=_DETAIL_COLUMNS)
    # Totals need the frame only, not a second copy of every detail row.
    details = id_counts = ids = None
    if detail.empty:
        return detail, pd.DataFrame(columns=_GROUP_COLUMNS), _totals(detail, include_nm_id=False)

    by_sku_rows: list[dict[str, Any]] = []
    for nm_id, group in detail.groupby("nm_id", sort=True, dropna=False):
        totals = _totals(group, include_nm_id=True)
        by_sku_rows.append({"nm_id": int(nm_id), **totals})
    by_sku = pd.DataFrame(by_sku_rows, columns=_GROUP_COLUMNS)
    store = _totals(detail, include_nm_id=False)
    return detail, by_sku, store
