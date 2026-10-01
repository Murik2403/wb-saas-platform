"""Month-by-month WB P&L built from the same rows and formulas as build_dashboard.

Every line is a plain sum of the realization-report rows (and ad/COGS rows) of
the month, so the monthly columns add up to the whole-period numbers that
``build_dashboard(...).financial`` produces for the same window. Management
estimate, not accounting.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

import pandas as pd

from db import read_financial, read_table
from db.financial_fifo_reconciliation import read_financial_fifo_reconciliation
from db.historical_cogs import project_historical_cogs
from financial_operations import financial_event_type, is_return_document, is_promotion_settlement

MAX_MONTHS = 24
_FIELDS = (
    "realization", "spp_discount", "sales", "cost", "logistics", "commission",
    "acquiring", "penalties", "storage", "advertising", "other", "result",
)
_NUMERIC = (
    "retail_amount", "retail_price_withdisc_rub", "ppvz_for_pay", "ppvz_sales_commission",
    "commission", "delivery_rub", "rebill_logistic_cost", "storage_fee", "penalty",
    "deduction", "acceptance", "additional_payment", "quantity", "acquiring_fee",
)


def month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    """Calendar months touched by [start, end], each clipped to the window."""
    out, cursor = [], date(start.year, start.month, 1)
    while cursor <= end:
        last = date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1])
        out.append((max(start, cursor), min(end, last)))
        cursor = last + timedelta(days=1)
    return out


def _ad_spend_by_day(start: date, end: date) -> pd.DataFrame:
    ads = read_table("ads_daily", omit_raw=True)
    if ads.empty:
        return pd.DataFrame(columns=["day", "spend"])
    ads["day"] = pd.to_datetime(ads["day"], errors="coerce").dt.date
    ads = ads.dropna(subset=["day"])
    ads = ads[(ads["day"] >= start) & (ads["day"] <= end)]
    # Same rule as build_dashboard: nm_id=0 rows are the authoritative total.
    total = ads[pd.to_numeric(ads["nm_id"], errors="coerce").fillna(0).astype(int) == 0]
    ads = total if not total.empty else ads
    ads = ads.assign(spend=pd.to_numeric(ads.get("spend", 0), errors="coerce").fillna(0))
    return ads[["day", "spend"]]


def monthly_pnl(start: date, end: date) -> dict:
    months = month_ranges(start, end)
    if len(months) > MAX_MONTHS:
        raise ValueError("period_too_long")
    f = read_financial(omit_raw=True).copy()
    cutoff = None
    f["operation_date"] = pd.to_datetime(f.get("operation_date"), errors="coerce")
    f = f.dropna(subset=["operation_date"])
    if not f.empty:
        cutoff = min(end, f["operation_date"].max().date())
    f = f[(f["operation_date"].dt.date >= start) & (f["operation_date"].dt.date <= end)]
    for col in _NUMERIC:
        if col not in f.columns:
            f[col] = 0.0
        f[col] = pd.to_numeric(f[col], errors="coerce").fillna(0)
    for col in ("doc_type_name", "operation_name"):
        if col not in f.columns:
            f[col] = ""
        f[col] = f[col].fillna("").astype(str)
    operation_rows = f[["doc_type_name", "operation_name"]].to_dict(orient="records")
    event_types = pd.Series([financial_event_type(row) for row in operation_rows], index=f.index, dtype=object)
    is_return, is_sale = event_types.eq("return"), event_types.eq("sale")
    return_documents = pd.Series([is_return_document(row) for row in operation_rows], index=f.index, dtype=bool)
    for col in ("retail_amount", "ppvz_for_pay", "ppvz_sales_commission", "commission", "acquiring_fee"):
        f.loc[return_documents, col] = -f.loc[return_documents, col].abs()
    ads = _ad_spend_by_day(start, end)
    if cutoff is not None and not ads.empty:
        ads = ads[ads["day"] <= cutoff]
    else:
        ads = ads.iloc[0:0]
    if not ads.empty:
        settlements = f.get("deduction_reason", pd.Series("", index=f.index)).map(is_promotion_settlement)
        f.loc[settlements, "deduction"] = 0.0
    units = f["quantity"].abs()
    f["signed_units"] = units.where(is_sale, 0).where(~is_return, -units)
    f["gross"] = f["retail_price_withdisc_rub"] * f["signed_units"]
    f["sale_u"] = units.where(is_sale, 0)
    f["return_u"] = units.where(is_return, 0)
    # ppvz_sales_commission is preferred; the legacy column only when it is all zero.
    f["comm"] = f["ppvz_sales_commission"] if f["ppvz_sales_commission"].abs().sum() >= 0.01 else f["commission"]
    f["ym"] = f["operation_date"].dt.strftime("%Y-%m")
    by_month = f.groupby("ym").sum(numeric_only=True)
    row_counts = f.groupby("ym").size()
    # build_dashboard falls back to gross when the whole period has no retail_amount.
    use_gross_as_sales = abs(float(f["retail_amount"].sum())) < 0.01

    rows = []
    for m_start, m_end in months:
        key = m_start.strftime("%Y-%m")
        g = by_month.loc[key] if key in by_month.index else None
        v = (lambda name: float(g[name]) if g is not None else 0.0)
        recon = read_financial_fifo_reconciliation(m_start.isoformat(), m_end.isoformat())
        cost = float(project_historical_cogs(recon)[2]["cogs_rub"])
        ad = float(ads.loc[(ads["day"] >= m_start) & (ads["day"] <= m_end), "spend"].sum()) if not ads.empty else 0.0
        gross = v("gross")
        sales = gross if use_gross_as_sales else v("retail_amount")
        to_pay = v("ppvz_for_pay")
        commission, acquiring = v("comm"), v("acquiring_fee")
        logistics = v("delivery_rub") + v("rebill_logistic_cost")
        # Everything WB took or paid that has no own line: reward, VAT, deductions,
        # acceptance, additional payments and rounding between report columns.
        other = (sales - to_pay - commission - acquiring) + v("deduction") + v("acceptance") - v("additional_payment")
        result = sales - cost - logistics - commission - acquiring - v("penalty") - v("storage_fee") - ad - other
        full_month = m_start.day == 1 and m_end == date(m_end.year, m_end.month, calendar.monthrange(m_end.year, m_end.month)[1])
        rows.append({
            "month": key, "start": m_start.isoformat(), "end": m_end.isoformat(),
            "partial": not full_month,
            # Report rows end earlier than this month's end: the tail is not loaded yet.
            "data_incomplete": cutoff is None or cutoff < m_end,
            "report_rows": int(row_counts.get(key, 0)),
            "units": v("signed_units"),
            "realization": gross, "spp_discount": sales - gross, "sales": sales,
            "cost": cost, "logistics": logistics, "commission": commission,
            "acquiring": acquiring, "penalties": v("penalty"), "storage": v("storage_fee"),
            "advertising": ad, "other": other, "result": result,
        })
    total = {name: sum(r[name] for r in rows) for name in _FIELDS + ("units", "report_rows")}
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "data_status": "ok" if len(f) else "no_financial_data",
        "data_through": cutoff.isoformat() if cutoff is not None else None,
        "months": rows,
        "total": total,
        "note": "Управленческая оценка по отчётам WB, не бухгалтерский учёт. Внешние ручные расходы не включены.",
    }
