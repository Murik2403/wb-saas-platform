"""Pure computation for the "Реклама" (Ads) screen.

Same role as today_service.py / overview_service.py: it wraps exactly what
pages/ads.py renders (the four ad KPI cards off data.kpi, plus the per-campaign
table data.ads that calculations.py already builds) and returns a
JSON-serializable dict for the Next.js frontend. Nothing here touches
Streamlit; pages/ads.py stays live and unchanged. See
memory/project_marketshelper_frontend_migration.md.

Unlike Обзор, this screen has no vs-previous-period comparison -- pages/ads.py
draws no delta badges -- so there is no prev_ctx here. It is still
period-scoped: `data` comes from build_dashboard(start, end) for whatever
window app.py's period selector resolves to, reusing overview_service's
resolve_window()/PERIOD_LABELS rather than re-deriving that date math.
"""

from __future__ import annotations

import math

from ui_helpers import money, num, pct
from today_service import json_safe, _records

# The one-line caveat pages/ads.py prints under the KPI row as st.caption --
# kept verbatim; it explains why these totals differ from a naive sum of the
# campaign table.
KPI_NOTE = (
    "Общие показатели считаются по итоговой строке каждой кампании за день — "
    "без суммирования дублей по приложениям и товарам."
)

# render_empty_state(...) call in pages/ads.py, as data instead of markup.
EMPTY_STATE = {
    "title": "Рекламных кампаний пока нет",
    "note": (
        "Статистика появится после первой синхронизации с включённой категорией "
        "«Продвижение» в токене WB, либо когда запустите рекламу в кабинете WB."
    ),
}

# calculations.py's ads_summary columns (Cyrillic, as st.dataframe shows them)
# -> API keys, in the same left-to-right order the Streamlit table uses.
_CAMPAIGN_COLUMNS = {
    "Кампания": "campaign",
    "Расход": "spend",
    "Показы": "views",
    "Клики": "clicks",
    "Заказы": "orders",
    "Рекламная выручка": "ad_revenue",
    "CTR, %": "ctr",
    "CPC": "cpc",
    "ДРР, %": "drr",
}


def _number(value: object) -> float:
    """Return a finite number for API payload calculations."""
    try:
        result = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _funnel(campaigns: list[dict]) -> list[dict]:
    """Build the advertising funnel from authoritative campaign totals."""
    views = sum(_number(row.get("views")) for row in campaigns)
    clicks = sum(_number(row.get("clicks")) for row in campaigns)
    orders = sum(_number(row.get("orders")) for row in campaigns)
    if views <= 0:
        return []
    return [
        {"label": "Показы", "value": round(views), "value_text": num(views), "width_pct": 100},
        {
            "label": "Клики", "value": round(clicks), "value_text": num(clicks),
            "width_pct": round(clicks / views * 100, 1),
        },
        {
            "label": "Заказы", "value": round(orders), "value_text": num(orders),
            "width_pct": round(orders / views * 100, 1),
        },
    ]


def _product_signals(data: object, campaigns: list[dict]) -> list[dict]:
    """Expose the products currently receiving ad spend, with campaign fallback."""
    products = _records(getattr(data, "products", None))
    signals: list[dict] = []
    for row in products:
        spend = _number(row.get("Реклама"))
        if spend <= 0:
            continue
        revenue = _number(row.get("Выручка"))
        drr = _number(row.get("ДРР, %"))
        label = str(row.get("Артикул продавца") or row.get("Товар") or row.get("Артикул WB") or "Товар")
        signals.append({
            "label": label,
            "value": f"ДРР {drr:.1f}% · {money(spend)}",
            "winner": revenue > 0 and drr <= 10,
            "spend": spend,
        })
    if signals:
        return [{key: value for key, value in signal.items() if key != "spend"}
                for signal in sorted(signals, key=lambda signal: signal["spend"], reverse=True)[:3]]

    # Older synchronizations may lack product allocation rows. The campaign
    # totals are still real data and make the panel actionable until the next
    # full ads sync restores product attribution.
    for row in sorted(campaigns, key=lambda campaign: _number(campaign.get("spend")), reverse=True)[:3]:
        spend = _number(row.get("spend"))
        revenue = _number(row.get("ad_revenue"))
        drr = _number(row.get("drr"))
        signals.append({
            "label": f"Кампания {row.get('campaign', '—')}",
            "value": f"ДРР {drr:.1f}% · {money(spend)}",
            "winner": revenue > 0 and drr <= 10,
        })
    return signals


def compute_ads(ctx: dict) -> dict:
    data = ctx["data"]
    k = data.kpi

    # The same four kpi_card() calls pages/ads.py makes, in the same order and
    # with the same pre-formatted value/note strings (money/num/pct), so the
    # frontend never re-implements the number formatting.
    kpis = [
        {
            "key": "ad_revenue", "label": "Сумма заказов",
            "value": money(k["ad_revenue"]), "note": "Атрибуция рекламного API",
        },
        {
            "key": "ad_spend", "label": "Затраты",
            "value": money(k["ad_spend"]), "note": "За выбранный период",
        },
        {
            "key": "ad_drr", "label": "Доля затрат",
            "value": pct(k["ad_drr"]), "note": "Затраты / сумма заказов",
        },
        {
            "key": "ad_ctr", "label": "Средний CTR",
            "value": pct(k["ad_ctr"]), "note": f"Клики {num(k['ad_clicks'])}",
        },
    ]

    campaigns = _records(data.ads, _CAMPAIGN_COLUMNS)

    return {
        "period": ctx["period"],
        "period_label": ctx.get("period_label", ctx["period"]),
        "start": json_safe(ctx["start"]),
        "end": json_safe(ctx["end"]),
        "kpis": json_safe(kpis),
        "kpi_note": KPI_NOTE,
        "empty_state": EMPTY_STATE,
        "campaigns": campaigns,
        "funnel": _funnel(campaigns),
        "creative_tests": _product_signals(data, campaigns),
    }
