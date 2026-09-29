"""Pure computation for the "Обзор" (Overview) screen.

The Overview counterpart to today_service.py: it wraps the exact same business
logic pages/overview.py renders (build_dashboard + the ui_helpers the page uses
-- delta_pct, the funnel stages, the problem-products classification) and
returns a JSON-serializable dict for the Next.js frontend. Nothing here touches
Streamlit; pages/overview.py stays live and unchanged. If the Overview page's
rules change, they change in one place -- here -- exactly as the Streamlit page
already computes them. See memory/project_marketshelper_frontend_migration.md.

The vs-previous-period comparison (KPI trend badges + the dashed revenue overlay
on the chart) needs a second, equal-length window immediately before the current
one -- pages/overview.py computes that window inside render(); api_server.py
builds it the same way and passes the resulting DashboardData in as `prev_ctx`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from ui_helpers import money, num, pct, delta_pct
from today_service import json_safe, _records

# The period control lives in app.py (above the page) for Streamlit, not in
# pages/overview.py -- these are app.py's own options, day counts and "30 дней"
# default, keyed by a URL-safe id for the API's `period` query param.
PERIOD_DAYS = {"today": 1, "7d": 7, "30d": 30, "90d": 90}
PERIOD_LABELS = {
    "today": "Сегодня", "7d": "7 дней", "30d": "30 дней",
    "90d": "90 дней", "custom": "Свои даты",
}
DEFAULT_PERIOD = "30d"


def resolve_window(period: str, today: date, start: date | None = None, end: date | None = None) -> tuple[date, date]:
    """(start, end) for a period id -- app.py's date math: a fixed-length window
    ending today, or the user's own two dates for "Свои даты", swapped back into
    order if they picked them backwards."""
    if period == "custom":
        if start is None or end is None:
            raise ValueError("custom period requires start and end")
    else:
        days = PERIOD_DAYS.get(period, PERIOD_DAYS[DEFAULT_PERIOD])
        end = today
        start = end - timedelta(days=days - 1)
    if start > end:
        start, end = end, start
    return start, end


def previous_window(start: date, end: date) -> tuple[date, date] | None:
    """The equal-length window immediately before [start, end] -- what the KPI
    trend badges and the chart's dashed revenue overlay compare against. Same
    arithmetic as pages/overview.py's render(). None when it would be empty."""
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return (prev_start, prev_end) if prev_end >= prev_start else None

# Same problem-status -> severity map render_problem_products_panel() uses, so
# the "Требует внимания" panel classifies rows identically to the Streamlit page.
_PROBLEM_STATUS_SEVERITY = {"Убыточный": "critical", "Низкая маржа": "warn"}

# Funnel + chart colors are the exact hex values pages/overview.py passes to
# render_funnel_bars() / px.line, mirrored here so the frontend renders the same
# palette rather than re-deriving one.
_FUNNEL_COLORS = {"orders": "#7c6cf6", "sales": "#3ecf8e", "returns": "#f2677a"}
_SERIES_META = [
    {"key": "order_amount", "name": "Сумма заказов", "color": "#7c6cf6"},
    {"key": "revenue", "name": "Выручка", "color": "#3ecf8e"},
    {"key": "ad_spend", "name": "Реклама", "color": "#f2b84b"},
]
_PREV_SERIES = {"key": "prev_revenue", "name": "Выручка (пред. период)", "color": "#8d94aa"}


def _f(value) -> float:
    """Scalar -> plain float, NaN/None -> 0.0. The chart series must be JSON
    numbers; a NaN leaking through would serialize as invalid JSON."""
    number = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(number) else float(number)


def _margin_pct(profit: float, revenue: float) -> float:
    """Return a safe percentage for a profit/revenue pair."""
    base = _f(revenue)
    return _f(profit) / abs(base) * 100 if abs(base) > 0.000001 else 0.0


def _problem_products(financial_products: pd.DataFrame, max_items: int = 5) -> list[dict]:
    """The worst-off products (loss-making / low-margin), ranked worst-first --
    the same selection render_problem_products_panel() makes: filter to the two
    problem statuses, sort by ascending Расчётная прибыль, take the top N."""
    if financial_products is None or financial_products.empty:
        return []
    problems = financial_products[financial_products["Статус"].isin(_PROBLEM_STATUS_SEVERITY)].copy()
    if problems.empty:
        return []
    problems = problems.sort_values("Расчётная прибыль").head(max_items)
    rows: list[dict] = []
    for _, row in problems.iterrows():
        status = str(row["Статус"])
        name = str(row.get("Товар") or row.get("Артикул продавца") or row.get("Артикул WB") or "")
        reason = str(row.get("Основная причина") or "")
        action = str(row.get("Рекомендация") or "")
        rows.append({
            "status": status,
            "severity": _PROBLEM_STATUS_SEVERITY.get(status, "warn"),
            "name": name,
            "profit": money(float(row.get("Расчётная прибыль", 0.0) or 0.0)),
            "reason": reason,
            "action": action,
        })
    return rows


def _chart(data, prev_data, is_today: bool) -> dict:
    """Line-chart payload: one row per time bucket with the series values the
    Streamlit px.line draws (order_amount / revenue / ad_spend, dropping ad_spend
    for the single-day "Сегодня" view), plus a position-aligned prev-period
    revenue overlay -- exactly as pages/overview.py builds prev_chart_df."""
    x_key = "hour" if is_today else "day"
    chart_df = (data.hourly if is_today else data.daily).copy()

    series = [s for s in _SERIES_META if not (is_today and s["key"] == "ad_spend")]
    value_keys = [s["key"] for s in series]

    prev_chart_df = (prev_data.hourly if is_today else prev_data.daily) if prev_data is not None else None
    has_prev = (
        prev_chart_df is not None
        and not prev_chart_df.empty
        and len(prev_chart_df) == len(chart_df)
    )
    prev_revenue = prev_chart_df["revenue"].tolist() if has_prev else None

    rows: list[dict] = []
    for i in range(len(chart_df)):
        src = chart_df.iloc[i]
        if is_today:
            x_label = f"{int(src['hour']):02d}:00"
        else:
            x_label = pd.Timestamp(src["day"]).strftime("%d.%m")
        row = {"x": x_label}
        for key in value_keys:
            row[key] = _f(src.get(key, 0))
        if prev_revenue is not None:
            row["prev_revenue"] = _f(prev_revenue[i])
        rows.append(row)

    if prev_revenue is not None:
        series = series + [_PREV_SERIES | {"dashed": True}]

    return {"x_key": x_key, "series": json_safe(series), "rows": json_safe(rows)}


def compute_overview(ctx: dict, prev_ctx: dict | None = None) -> dict:
    data = ctx["data"]
    period = ctx["period"]
    period_label = ctx.get("period_label", period)
    start, end = ctx["start"], ctx["end"]
    is_today = start == end

    prev_data = prev_ctx["data"] if prev_ctx is not None else None
    prev_start = prev_ctx["start"] if prev_ctx is not None else None
    prev_end = prev_ctx["end"] if prev_ctx is not None else None
    prev_kpi = prev_data.kpi if prev_data is not None else {}
    prev_profit = prev_data.financial.get("profit") if prev_data is not None else None

    k = data.kpi
    profit_value = data.financial.get("profit", 0.0)
    realization_revenue = data.financial.get("operational_buyout_amount") or k.get("revenue", 0.0)
    order_amount = k.get("order_amount", 0.0)
    net_margin_pct = _margin_pct(profit_value, realization_revenue)
    orders_profitability_pct = _margin_pct(profit_value, order_amount)
    prev_realization_revenue = (prev_data.financial.get("operational_buyout_amount") or prev_kpi.get("revenue", 0.0)) if prev_data is not None else None
    prev_order_amount = prev_kpi.get("order_amount") if prev_data is not None else None
    prev_net_margin_pct = _margin_pct(prev_profit, prev_realization_revenue) if prev_data is not None else None
    prev_orders_profitability_pct = _margin_pct(prev_profit, prev_order_amount) if prev_data is not None else None

    # Same seven cards, in the same order, as pages/overview.py's hero + 6 KPI
    # columns. `spark_key` names the daily series the frontend draws beneath the
    # card (matching kpi_card_with_sparkline's `series` arg); None = no sparkline.
    kpis = [
        {
            "key": "profit", "hero": True,
            "label": "Прибыль по товарам (оценка)",
            "value": money(profit_value),
            "note": "Расчётная, за период · точная цифра — в «Финансы»",
            "delta": delta_pct(profit_value, prev_profit),
            "spark_key": None,
        },
        {
            "key": "orders", "hero": False, "label": "Заказы",
            "value": num(k["orders"]), "note": money(k["order_amount"]),
            "delta": delta_pct(k["orders"], prev_kpi.get("orders")),
            "spark_key": "orders",
        },
        {
            "key": "sales", "hero": False, "label": "Выкупы",
            "value": num(k["sales"]), "note": f"Оперативный API · {pct(k['buyout'])}",
            "delta": delta_pct(k["sales"], prev_kpi.get("sales")),
            "spark_key": "sales",
        },
        {
            "key": "returns", "hero": False, "label": "Возвраты",
            "value": num(k["returns"]), "note": "За выбранный период",
            "delta": delta_pct(k["returns"], prev_kpi.get("returns")),
            "spark_key": None,
        },
        {
            "key": "revenue", "hero": False, "label": "Выручка",
            "value": money(k["revenue"]), "note": "Оперативные данные",
            "delta": delta_pct(k["revenue"], prev_kpi.get("revenue")),
            "spark_key": "revenue",
        },
        {
            "key": "net_margin_pct", "hero": False, "label": "Расчётная маржинальность",
            "value": pct(net_margin_pct),
            "note": "Результат по учтённым затратам / выручка реализации · полнота и оценка себестоимости — в Финансах",
            "delta": delta_pct(net_margin_pct, prev_net_margin_pct),
            "spark_key": None,
        },
        {
            "key": "orders_profitability_pct", "hero": False, "label": "Рентабельность заказов",
            "value": pct(orders_profitability_pct),
            "note": "Результат по учтённым затратам / сумма заказов · возвраты и доступные расходы WB",
            "delta": delta_pct(orders_profitability_pct, prev_orders_profitability_pct),
            "spark_key": None,
        },
        {
            "key": "ad_spend", "hero": False, "label": "Реклама",
            "value": money(k["ad_spend"]), "note": f"ДРР {pct(k['drr'])}",
            "delta": delta_pct(k["ad_spend"], prev_kpi.get("ad_spend")),
            "spark_key": "ad_spend", "tone": "warn",
        },
        {
            "key": "stock", "hero": False, "label": "Остаток",
            "value": num(k["stock"]), "note": "На складах WB",
            "delta": None, "spark_key": None,
        },
    ]

    # Same three stages and the same bar-width math as render_funnel_bars():
    # width is relative to the widest stage, floored at 4% so a zero/tiny stage
    # is still visible as a stub rather than disappearing entirely.
    funnel_stages = [
        ("Заказано", _f(k["orders"]), _FUNNEL_COLORS["orders"]),
        ("Выкуплено", _f(k["sales"]), _FUNNEL_COLORS["sales"]),
        ("Возврат", _f(k["returns"]), _FUNNEL_COLORS["returns"]),
    ]
    funnel_max = max((v for _, v, _ in funnel_stages), default=0) or 1
    funnel = [
        {
            "label": label, "value": value, "value_text": num(value), "color": color,
            "width_pct": max(4.0, min(100.0, value / funnel_max * 100)),
        }
        for label, value, color in funnel_stages
    ]

    products_view = pd.DataFrame()
    if not data.products.empty:
        products_view = data.products[[
            "Артикул WB", "Артикул продавца", "Заказы", "Продажи",
            "Выручка", "ДРР, %", "Остаток", "Запас, дней",
        ]].copy()

    observed_daily = data.daily[pd.to_numeric(data.daily.get("revenue", 0), errors="coerce").fillna(0) > 0] if not data.daily.empty else data.daily
    latest_revenue = _f(observed_daily.iloc[-1].get("revenue", 0)) if not observed_daily.empty else 0.0
    week_revenue = _f(observed_daily.tail(7).get("revenue", pd.Series(dtype=float)).sum()) if not observed_daily.empty else 0.0
    cash_windows = [
        {"label": "Последний день", "value": money(latest_revenue), "detail": "выручка WB по оперативным данным"},
        {"label": "7 дней", "value": money(week_revenue), "detail": "выручка WB за последние загруженные дни"},
        {"label": "Расчётная прибыль", "value": money(profit_value), "detail": "за выбранный период"},
    ]

    return {
        "period": period,
        "period_label": period_label,
        "start": json_safe(start),
        "end": json_safe(end),
        "prev_start": json_safe(prev_start),
        "prev_end": json_safe(prev_end),
        "has_prev": prev_data is not None,
        "kpis": json_safe(kpis),
        "funnel": json_safe(funnel),
        "chart": _chart(data, prev_data, is_today),
        "cash_windows": cash_windows,
        "problem_products": _problem_products(data.financial_products),
        # The secondary operational signals render_problem_products_panel() puts
        # behind its "Ещё операционные сигналы" expander (low stock, high ДРР,
        # low buyout) -- classified in calculations.py, listed as-is here.
        "alerts": _records(data.alerts, {
            "Приоритет": "priority", "Артикул WB": "nm_id", "Сигнал": "signal",
        }),
        "products": _records(products_view, {
            "Артикул WB": "nm_id", "Артикул продавца": "article",
            "Заказы": "orders", "Продажи": "sales", "Выручка": "revenue",
            "ДРР, %": "drr", "Остаток": "stock", "Запас, дней": "days_of_stock",
        }),
    }
