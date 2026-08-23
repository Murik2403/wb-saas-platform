from __future__ import annotations

import hashlib
import math
import time
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from db import (
    finished_goods_fifo_guard_status, get_production_capacity, read_financial,
    read_finished_goods_fifo_summary, read_material_fifo_summary, read_table,
    sales_fifo_tracking_status,
)

def money(v: float) -> str:
    return f"{v:,.0f} ₽".replace(",", " ")


def num(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def pct(v: float) -> str:
    return f"{v:.1f}%"


# NOTE: this used to also contain a PRODUCTION_RULES lookup table + an
# "Автонормы" auto-fill feature (production_rule()/apply_production_rules())
# that hardcoded one specific tenant's own material-consumption numbers for
# two named blank types. Removed so the production module works for any
# manufacturer of any physical product: blank type, pack size, material
# consumption and minimum batch are now always plain fields the tenant
# fills in themselves in Settings -- see pages/settings_page.py.


# Raw material can be tracked two ways per material_inventory_color row (see
# db/core.py schema and pages/settings_page.py section "Остатки сырья"):
#   - 'packaged': stock is N whole packages of a fixed size (roll_length) plus a
#     partial leftover -- the original model, named for rolls of fabric/film/etc.
#     but equally applies to bags, boxes, spools of any other fixed-size unit.
#   - 'quantity': stock is just one running number, for material that isn't sold
#     in fixed-size packages (loose kg, litres, pieces bought ad hoc, ...).
# 'quantity' materials are stored with full_rolls pinned at 0 and roll_length
# pinned at this sentinel (see db/production.py's save_material_inventory) so the
# existing "full_rolls * roll_length + partial_meters" stock formula used
# everywhere (db/production.py's _apply_material_delta, db/fifo_materials.py's
# _physical_material_total, and every page below) keeps working completely
# unchanged -- it degenerates to "partial_meters is the whole quantity" with no
# code changes needed there. What DOES need to check for this sentinel is any
# calculation that uses roll_length as a *scale* (a "buy N packages" suggestion,
# or a "% of one package" low-stock threshold) rather than just as an additive
# term, since dividing by/against the sentinel would silently produce nonsense
# (e.g. math.ceil() of any tiny positive shortfall over the sentinel is still 1,
# not 0). Use is_packaged_material()/packages_to_buy() below for that.
NO_PACKAGE_ROLL_LENGTH = 1_000_000_000.0


def is_packaged_material(roll_length) -> bool:
    """True if roll_length reflects a real fixed package size, not the
    NO_PACKAGE_ROLL_LENGTH sentinel used for plain-quantity tracking."""
    try:
        return float(roll_length or 0) < NO_PACKAGE_ROLL_LENGTH / 2
    except (TypeError, ValueError):
        return True


def packages_to_buy(shortage: float, roll_length, balance_known: bool) -> int:
    """How many whole packages to buy to cover `shortage`, or 0 when the
    material isn't tracked in fixed-size packages (or stock isn't confirmed)."""
    if not balance_known or not is_packaged_material(roll_length):
        return 0
    shortage = max(0.0, float(shortage or 0))
    if shortage <= 0:
        return 0
    return int(math.ceil(shortage / max(float(roll_length or 0.1), 0.1)))


def material_unit_label(unit) -> str:
    """Normalize a possibly-blank unit label to a safe default for display."""
    text = str(unit or "").strip()
    return text or "м"


def infer_material_name(supplier_article: str, product_name: str = "") -> str:
    text = f"{supplier_article} {product_name}".casefold()
    rules = [
        (("cream", "beige", "bez", "беж"), "Бежевый"),
        (("white", "бел"), "Белый"),
        (("black", "черн"), "Чёрный"),
        (("grey", "gray", "сер"), "Серый"),
        (("brown", "корич"), "Коричневый"),
        (("green", "зел"), "Зелёный"),
        (("blue", "sini", "син"), "Синий"),
        (("bord", "burg", "бордо", "бордов"), "Бордовый"),
        (("pink", "роз"), "Розовый"),
        (("pist", "фист"), "Фисташковый"),
    ]
    for tokens, label in rules:
        if any(token in text for token in tokens):
            return label
    return "Не указан"


def material_key(material_name: str) -> str:
    return str(material_name or "").strip().casefold()


def ceil_to_batch(value: float, batch: int) -> int:
    value = max(0.0, float(value or 0))
    batch = max(1, int(batch or 1))
    return int(math.ceil(value / batch) * batch) if value > 0 else 0


def delta_pct(current: float, previous: float | None) -> float | None:
    """% change vs. a comparable previous period. None (not 0%) when there's no
    baseline to compare against, so callers can skip the badge instead of
    showing a misleading "+inf%"."""
    if previous is None or abs(previous) < 1e-9:
        return None
    return (float(current) - float(previous)) / abs(float(previous)) * 100


def _delta_badge_html(value: float | None) -> str:
    if value is None:
        return ""
    cls = "good" if value > 0.05 else ("critical" if value < -0.05 else "neutral")
    arrow = "▲" if value > 0.05 else ("▼" if value < -0.05 else "•")
    return f'<span class="kpi-delta {cls}">{arrow} {abs(value):.1f}%</span>'


def kpi_card(label: str, value: str, note: str = "", hero: bool = False, delta: float | None = None) -> None:
    css_class = "kpi-card kpi-card-hero" if hero else "kpi-card"
    st.markdown(
        f'<div class="{css_class}"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value} {_delta_badge_html(delta)}</div>'
        f'<div class="kpi-note">{note}</div></div>',
        unsafe_allow_html=True,
    )


def kpi_card_with_sparkline(
    label: str, value: str, note: str, series: pd.Series, key: str,
    color: str = "#7c6cf6", delta: float | None = None,
) -> None:
    """Same as kpi_card, plus a tiny bar sparkline of the period's daily trend
    directly beneath it -- borrowed (adapted to our dark palette, not copied
    wholesale) from two CRM dashboard references the user linked: the
    sparkline+card from Trackify, and the vs-previous-period trend badge
    from Oripio's Nexo Co sales dashboard.
    """
    kpi_card(label, value, note, delta=delta)
    values = pd.to_numeric(series, errors="coerce").fillna(0.0) if series is not None else pd.Series(dtype=float)
    if values.empty or values.abs().sum() == 0:
        return
    fig = go.Figure(go.Bar(x=list(range(len(values))), y=values.tolist(), marker_color=color, marker_line_width=0))
    fig.update_layout(
        height=36,
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=False,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        bargap=0.25,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)


_PROBLEM_STATUS_SEVERITY = {"Убыточный": "critical", "Низкая маржа": "warn"}


def render_problem_products_panel(
    financial_products: pd.DataFrame,
    alerts: pd.DataFrame,
    max_items: int = 5,
) -> None:
    """Surfaces the worst-off products (loss-making / low-margin) plus operational
    alerts (low stock, high ad spend, low buyout) as ranked status-pill rows,
    instead of a raw dataframe -- the underlying risk_reason/status classification
    already happens in calculations.py, this just makes it scannable at a glance.
    """
    problems = pd.DataFrame()
    if financial_products is not None and not financial_products.empty:
        problems = financial_products[financial_products["Статус"].isin(_PROBLEM_STATUS_SEVERITY)].copy()
        problems = problems.sort_values("Расчётная прибыль").head(max_items)

    if problems.empty and (alerts is None or alerts.empty):
        st.success("Критичных сигналов за выбранный период нет.")
        return

    for _, row in problems.iterrows():
        severity = _PROBLEM_STATUS_SEVERITY.get(row["Статус"], "warn")
        name = escape(str(row.get("Товар") or row.get("Артикул продавца") or row.get("Артикул WB")))
        profit = money(row["Расчётная прибыль"])
        reason = escape(str(row.get("Основная причина") or ""))
        action = escape(str(row.get("Рекомендация") or ""))
        st.markdown(
            f'<div class="problem-row">'
            f'<span class="status-pill {severity}">{escape(row["Статус"])}</span> '
            f'<strong>{name}</strong> · {profit}'
            f'<div class="problem-note">{reason}. {action}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    if alerts is not None and not alerts.empty:
        with st.expander(f"Ещё операционные сигналы ({len(alerts)})", expanded=problems.empty):
            st.dataframe(alerts, hide_index=True, use_container_width=True)


def render_funnel_bars(stages: list[tuple[str, float, str]]) -> None:
    """Horizontal gradient-bar funnel (label, value, color per stage), widest
    stage first -- e.g. Заказано -> Выкуплено -> Возврат. Adapted from the
    pipeline-stage bars in Oripio's Nexo Co sales dashboard reference.
    """
    max_value = max((v for _, v, _ in stages), default=0) or 1
    rows = []
    for label, value, color in stages:
        width_pct = max(4.0, min(100.0, value / max_value * 100))
        rows.append(
            f'<div class="funnel-row">'
            f'<div class="funnel-label">{escape(label)}</div>'
            f'<div class="funnel-track"><div class="funnel-fill" style="width:{width_pct:.1f}%; background:{color};"></div></div>'
            f'<div class="funnel-value">{num(value)}</div>'
            f'</div>'
        )
    st.markdown('<div class="funnel">' + "".join(rows) + "</div>", unsafe_allow_html=True)


def render_setup_checklist(token_exists: bool, sync_info: dict | None) -> None:
    """First-run onboarding checklist shown instead of a flat "нет данных"
    message when no orders/sales exist yet -- a new seller landing on an
    empty dashboard should see concrete next steps and their progress, not
    just one line of text. Borrowed from setup-checklist patterns common in
    SaaS onboarding dashboards (e.g. Wix's post-signup "welcome" checklist).
    """
    steps = [
        ("Подключить токен Wildberries", bool(token_exists), "Настройки → API токен"),
        ("Дождаться первой синхронизации", sync_info is not None, "Обычно занимает несколько минут после подключения токена"),
        ("Проверить себестоимость товаров", False, "Настройки → Себестоимость — сверьте автозаполненные значения"),
    ]
    rows = []
    for label, done, note in steps:
        cls = "good" if done else "neutral"
        icon = "✓" if done else "○"
        rows.append(
            f'<div class="checklist-row">'
            f'<span class="checklist-icon {cls}">{icon}</span>'
            f'<div><div class="checklist-label">{escape(label)}</div><div class="checklist-note">{escape(note)}</div></div>'
            f'</div>'
        )
    st.markdown('<div class="checklist">' + "".join(rows) + "</div>", unsafe_allow_html=True)


def render_empty_state(title: str, note: str, icon: str = "○") -> None:
    """A centered, slightly more considered "nothing here yet" panel than a
    plain st.info() -- for pages like Реклама/Закупки with no data yet."""
    st.markdown(
        f'<div class="empty-state">'
        f'<div class="empty-state-icon">{icon}</div>'
        f'<div class="empty-state-title">{escape(title)}</div>'
        f'<div class="empty-state-note">{escape(note)}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _parse_local_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        py_dt = parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed
        if py_dt.tzinfo is None:
            return py_dt.replace(tzinfo=ZoneInfo("Europe/Moscow"))
        return py_dt.astimezone(ZoneInfo("Europe/Moscow"))
    except Exception:
        return None


def _quality_row(
    module: str,
    check: str,
    status: str,
    detail: str,
    action: str,
    weight: float,
    required: bool = True,
) -> dict[str, object]:
    status_score = {"Готово": 1.0, "Ожидание API": 1.0, "Внимание": 0.55, "Критично": 0.0, "Отложено": 0.0}.get(status, 0.0)
    return {
        "Модуль": module,
        "Проверка": check,
        "Статус": status,
        "Детали": detail,
        "Что сделать": action,
        "Вес": float(weight),
        "Обязательная": bool(required),
        "Баллы": float(weight) * status_score if required else 0.0,
    }



def _normalize_supplier_article(value: object) -> str:
    """Return a stable key for seller articles used only as a fallback identity."""
    text = str(value or "").replace("\u00a0", " ").replace("\u200b", " ")
    return "".join(text.split()).casefold()


def _positive_int_set(values: pd.Series | list | tuple) -> set[int]:
    numeric = pd.to_numeric(pd.Series(values), errors="coerce").dropna().astype(int)
    return set(numeric[numeric.gt(0)].tolist())


def _cost_coverage_diagnostics(
    catalog: pd.DataFrame,
    orders: pd.DataFrame,
    sales: pd.DataFrame,
    stocks: pd.DataFrame,
    costs: pd.DataFrame,
    now_msk: datetime,
) -> tuple[dict[str, object], pd.DataFrame]:
    """Resolve cost coverage by WB article first and seller article second.

    The cost editor is formed from the current WB catalog, therefore historical
    operations for cards that are no longer in that catalog must not create a
    false warning in the current-assortment readiness score. They are counted
    separately for transparency.
    """
    active_sources: dict[int, set[str]] = {}

    def add_ids(frame: pd.DataFrame, label: str) -> None:
        if frame.empty or "nm_id" not in frame.columns:
            return
        for nm_id in _positive_int_set(frame["nm_id"]):
            active_sources.setdefault(nm_id, set()).add(label)

    # Current positive stock from the latest snapshot.
    stock_current = stocks.copy()
    if not stock_current.empty and "nm_id" in stock_current.columns:
        if "snapshot_at" in stock_current.columns:
            stock_current["snapshot_at"] = pd.to_datetime(stock_current["snapshot_at"], errors="coerce")
            latest_snapshot = stock_current["snapshot_at"].max()
            if pd.notna(latest_snapshot):
                stock_current = stock_current[stock_current["snapshot_at"].eq(latest_snapshot)]
        if "quantity" in stock_current.columns:
            stock_current = stock_current[pd.to_numeric(stock_current["quantity"], errors="coerce").fillna(0).gt(0)]
        add_ids(stock_current, "Остаток WB")

    # Recent orders and sales.
    cutoff = now_msk.date() - timedelta(days=89)
    for frame, date_col, label in ((orders, "order_date", "Заказы 90 дней"), (sales, "sale_date", "Продажи 90 дней")):
        if frame.empty or "nm_id" not in frame.columns:
            continue
        current = frame.copy()
        if date_col in current.columns:
            current[date_col] = pd.to_datetime(current[date_col], errors="coerce").dt.date
            current = current[current[date_col].notna() & current[date_col].ge(cutoff)]
        add_ids(current, label)

    operational_ids = set(active_sources)
    catalog_ids = _positive_int_set(catalog["nm_id"]) if not catalog.empty and "nm_id" in catalog.columns else set()
    if catalog_ids:
        active_ids = operational_ids & catalog_ids if operational_ids else catalog_ids
        historical_outside_catalog = operational_ids - catalog_ids
    else:
        active_ids = operational_ids
        historical_outside_catalog = set()

    # Identity map. Current catalog has priority, recent operations fill gaps.
    identity: dict[int, dict[str, str]] = {}
    for frame in (catalog, orders, sales):
        if frame.empty or "nm_id" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            try:
                nm_id = int(float(row.get("nm_id", 0) or 0))
            except (TypeError, ValueError):
                continue
            if nm_id <= 0:
                continue
            item = identity.setdefault(nm_id, {"supplier_article": "", "product_name": ""})
            article = str(row.get("supplier_article", "") or "").strip()
            product = str(row.get("product_name", row.get("subject", "")) or "").strip()
            if article and not item["supplier_article"]:
                item["supplier_article"] = article
            if product and not item["product_name"]:
                item["product_name"] = product

    positive_by_nm: dict[int, float] = {}
    zero_by_nm: set[int] = set()
    positive_by_article: dict[str, float] = {}
    zero_by_article: set[str] = set()
    if not costs.empty:
        for _, row in costs.iterrows():
            try:
                nm_id = int(float(row.get("nm_id", 0) or 0))
            except (TypeError, ValueError):
                nm_id = 0
            rate = float(pd.to_numeric(pd.Series([row.get("cost_per_wb_unit", 0)]), errors="coerce").fillna(0).iloc[0])
            article_key = _normalize_supplier_article(row.get("supplier_article", ""))
            if nm_id > 0:
                if rate > 0:
                    positive_by_nm[nm_id] = rate
                else:
                    zero_by_nm.add(nm_id)
            if article_key:
                if rate > 0:
                    # A positive saved rate always wins over a stale zero row.
                    positive_by_article[article_key] = rate
                    zero_by_article.discard(article_key)
                elif article_key not in positive_by_article:
                    zero_by_article.add(article_key)

    diagnostic_rows: list[dict[str, object]] = []
    matched_nm = matched_article = zero_count = missing_count = 0
    for nm_id in sorted(active_ids):
        item = identity.get(nm_id, {})
        article = str(item.get("supplier_article", "") or "").strip()
        article_key = _normalize_supplier_article(article)
        product_name = str(item.get("product_name", "") or "").strip()
        sources = ", ".join(sorted(active_sources.get(nm_id, {"Каталог"})))
        if nm_id in positive_by_nm:
            rate = positive_by_nm[nm_id]
            match = "По артикулу WB"
            status = "Покрыт"
            matched_nm += 1
        elif article_key and article_key in positive_by_article:
            rate = positive_by_article[article_key]
            match = "По артикулу продавца"
            status = "Покрыт"
            matched_article += 1
        elif nm_id in zero_by_nm or (article_key and article_key in zero_by_article):
            rate = 0.0
            match = "Строка найдена"
            status = "Нулевая ставка"
            zero_count += 1
        else:
            rate = 0.0
            match = "Совпадение не найдено"
            status = "Нет строки"
            missing_count += 1
        diagnostic_rows.append({
            "Артикул WB": nm_id,
            "Артикул продавца": article,
            "Товар": product_name,
            "Источник активности": sources,
            "Ставка, ₽": rate,
            "Сопоставление": match,
            "Статус": status,
        })

    diagnostics = pd.DataFrame(diagnostic_rows)
    if not diagnostics.empty:
        status_rank = {"Нулевая ставка": 0, "Нет строки": 1, "Покрыт": 2}
        diagnostics["_rank"] = diagnostics["Статус"].map(status_rank).fillna(9)
        diagnostics = diagnostics.sort_values(["_rank", "Артикул продавца", "Артикул WB"]).drop(columns="_rank")

    covered = matched_nm + matched_article
    coverage = covered / len(active_ids) * 100 if active_ids else 0.0
    return {
        "active_ids": active_ids,
        "active_count": len(active_ids),
        "covered": covered,
        "coverage": coverage,
        "matched_nm": matched_nm,
        "matched_article": matched_article,
        "zero_count": zero_count,
        "missing_count": missing_count,
        "historical_outside_catalog": len(historical_outside_catalog),
    }, diagnostics


def build_data_quality_overview(
    app_settings: dict,
    token_available: bool,
    sync_state: dict | None,
    backup_state: dict | None,
) -> tuple[pd.DataFrame, dict[str, object], pd.DataFrame, pd.DataFrame]:
    """Assess whether operational recommendations are sufficiently grounded.

    Supplier data is intentionally optional: an unconfigured procurement module
    must not reduce the reliability score of finance, production, stock or FIFO.
    """
    rows: list[dict[str, object]] = []
    now_msk = datetime.now(ZoneInfo("Europe/Moscow"))

    # API and freshness.
    rows.append(_quality_row(
        "Интеграция", "Токен WB API",
        "Готово" if token_available else "Критично",
        "Токен сохранён локально." if token_available else "Токен не найден.",
        "Ничего делать не нужно." if token_available else "Сохранить токен в настройках.",
        10,
    ))
    sync_finished = _parse_local_datetime((sync_state or {}).get("finished_at") or (sync_state or {}).get("started_at"))
    sync_status = str((sync_state or {}).get("status") or "").casefold()
    if sync_status in {"error", "failed", "ошибка"}:
        sync_quality = ("Критично", f"Последняя синхронизация завершилась ошибкой: {(sync_state or {}).get('details','')}", "Повторить синхронизацию и проверить текст ошибки.")
    elif sync_finished is None:
        sync_quality = ("Критично", "Успешная синхронизация ещё не зафиксирована.", "Запустить синхронизацию сейчас.")
    else:
        age_hours = max(0.0, (now_msk - sync_finished).total_seconds() / 3600)
        if age_hours <= 3:
            sync_quality = ("Готово", f"Данные обновлены {age_hours:.1f} ч. назад.", "Ничего делать не нужно.")
        elif age_hours <= 24:
            sync_quality = ("Внимание", f"Данные обновлены {age_hours:.1f} ч. назад.", "Обновить данные перед принятием оперативных решений.")
        else:
            sync_quality = ("Критично", f"Данные не обновлялись {age_hours:.1f} ч.", "Запустить синхронизацию до работы с рекомендациями.")
    rows.append(_quality_row("Интеграция", "Свежесть данных WB", *sync_quality, 14))

    # Core tables and active assortment.
    catalog = read_table("products_catalog")
    orders = read_table("orders")
    sales = read_table("sales")
    stocks = read_table("stocks")
    ads = read_table("ads_daily")
    costs = read_table("costs")
    production = read_table("production_settings")
    materials = read_table("material_inventory_color")
    pipeline = read_table("product_pipeline")
    financial = read_financial()

    catalog_count = int(len(catalog))
    rows.append(_quality_row(
        "Каталог", "Карточки WB",
        "Готово" if catalog_count > 0 else "Критично",
        f"В каталоге {catalog_count} карточек.",
        "Ничего делать не нужно." if catalog_count > 0 else "Синхронизировать каталог WB.",
        7,
    ))
    operations_count = int(len(orders) + len(sales))
    rows.append(_quality_row(
        "Продажи", "Заказы и продажи",
        "Готово" if operations_count > 0 else "Критично",
        f"Загружено операций: {operations_count:,}.".replace(",", " "),
        "Ничего делать не нужно." if operations_count > 0 else "Загрузить историю заказов и продаж.",
        9,
    ))

    cost_summary, cost_diagnostics = _cost_coverage_diagnostics(
        catalog, orders, sales, stocks, costs, now_msk,
    )
    active_ids: set[int] = set(cost_summary["active_ids"])
    cost_coverage = float(cost_summary["coverage"])
    missing_costs = int(cost_summary["missing_count"] + cost_summary["zero_count"])
    if active_ids and missing_costs == 0:
        cost_status = "Готово"
    elif active_ids and cost_coverage >= 85:
        cost_status = "Внимание"
    else:
        cost_status = "Критично"
    detail_parts = [
        f"Покрытие {cost_coverage:.1f}%: {int(cost_summary['covered'])} из {int(cost_summary['active_count'])}",
        f"по артикулу WB — {int(cost_summary['matched_nm'])}",
        f"по артикулу продавца — {int(cost_summary['matched_article'])}",
        f"нулевая ставка — {int(cost_summary['zero_count'])}",
        f"строка не найдена — {int(cost_summary['missing_count'])}",
    ]
    historical_count = int(cost_summary["historical_outside_catalog"])
    if historical_count:
        detail_parts.append(f"исторических карточек вне текущего каталога исключено — {historical_count}")
    rows.append(_quality_row(
        "Финансы", "Себестоимость активных товаров", cost_status,
        "; ".join(detail_parts) + ".",
        "Ничего делать не нужно." if cost_status == "Готово" else "Открыть диагностику ниже и заполнить только перечисленные позиции.",
        13,
    ))

    fin_count = int(len(financial))
    if fin_count > 0 and "operation_date" in financial.columns:
        fin_dates = pd.to_datetime(financial["operation_date"], errors="coerce")
        fin_last = fin_dates.max()
        fin_age = (now_msk.date() - fin_last.date()).days if pd.notna(fin_last) else 9999
    else:
        fin_age = 9999
    if fin_count <= 0:
        fin_status, fin_action = "Критично", "Загрузить финансовый отчёт WB."
    elif fin_age <= 10:
        fin_status, fin_action = "Готово", "Ничего делать не нужно."
    elif fin_age <= 21:
        fin_status, fin_action = "Внимание", "Обновить финансовый отчёт после закрытия новых недель."
    else:
        fin_status, fin_action = "Критично", "Загрузить актуальный финансовый отчёт WB."
    rows.append(_quality_row(
        "Финансы", "Финансовый отчёт WB", fin_status,
        f"Строк: {fin_count:,}; последняя операция {fin_age} дн. назад.".replace(",", " ") if fin_count else "Отчёт не загружен.",
        fin_action, 13,
    ))

    # Advertising freshness: required only for advertising recommendations.
    if ads.empty:
        ad_status, ad_detail, ad_action = "Внимание", "Рекламные данные отсутствуют.", "Синхронизировать рекламу перед изменением ставок."
    else:
        ad_last = pd.to_datetime(ads.get("day"), errors="coerce").max()
        ad_age = (now_msk.date() - ad_last.date()).days if pd.notna(ad_last) else 9999
        if ad_age <= 2:
            ad_status, ad_action = "Готово", "Ничего делать не нужно."
        elif ad_age <= 7:
            ad_status, ad_action = "Внимание", "Обновить рекламу перед изменением ставок."
        else:
            ad_status, ad_action = "Критично", "Синхронизировать рекламные кампании."
        ad_detail = f"Последний рекламный день: {ad_last.date():%d.%m.%Y}; задержка {ad_age} дн." if pd.notna(ad_last) else "Дата рекламных данных не определена."
    rows.append(_quality_row("Реклама", "Свежесть рекламных расходов", ad_status, ad_detail, ad_action, 7))

    # Production and raw materials.
    enabled_prod = production[pd.to_numeric(production.get("enabled", 0), errors="coerce").fillna(0).astype(int).eq(1)].copy() if not production.empty else pd.DataFrame()
    if enabled_prod.empty:
        rows.append(_quality_row("Производство", "Производственные нормы", "Отложено", "Собственное производство не включено.", "Проверка не влияет на общий балл.", 0, required=False))
        required_materials: set[str] = set()
    else:
        valid_prod = (
            pd.to_numeric(enabled_prod.get("material_per_unit", 0), errors="coerce").fillna(0).gt(0)
            & enabled_prod.get("material_name", pd.Series("", index=enabled_prod.index)).fillna("").astype(str).str.strip().ne("")
        )
        prod_coverage = float(valid_prod.mean() * 100) if len(valid_prod) else 0.0
        prod_status = "Готово" if prod_coverage >= 99.9 else ("Внимание" if prod_coverage >= 85 else "Критично")
        rows.append(_quality_row(
            "Производство", "Производственные нормы", prod_status,
            f"Настроено {int(valid_prod.sum())} из {len(enabled_prod)} производимых карточек ({prod_coverage:.1f}%).",
            "Ничего делать не нужно." if prod_status == "Готово" else "Заполнить тип заготовки, комплект, материал и норму расхода.",
            8,
        ))
        required_materials = set(enabled_prod.get("material_name", pd.Series(dtype=str)).fillna("").astype(str).str.strip()) - {""}

    known_materials: set[str] = set()
    if not materials.empty:
        known_mask = pd.to_numeric(materials.get("balance_known", 0), errors="coerce").fillna(0).astype(int).eq(1)
        known_materials = set(materials.loc[known_mask, "material_name"].fillna("").astype(str).str.strip()) - {""}
    if required_materials:
        mat_coverage = len(required_materials & known_materials) / len(required_materials) * 100
        mat_status = "Готово" if mat_coverage >= 99.9 else ("Внимание" if mat_coverage >= 75 else "Критично")
        missing_material_names = sorted(required_materials - known_materials)
        rows.append(_quality_row(
            "Производство", "Остатки сырья", mat_status,
            f"Известны остатки по {len(required_materials & known_materials)} из {len(required_materials)} используемых цветов."
            + (f" Не указаны: {', '.join(missing_material_names[:5])}." if missing_material_names else ""),
            "Ничего делать не нужно." if mat_status == "Готово" else "Указать физические остатки сырья по недостающим цветам.",
            8,
        ))
        raw_fifo = read_material_fifo_summary()
        raw_diff = float(pd.to_numeric(raw_fifo.get("difference_meters", 0), errors="coerce").fillna(0).abs().sum()) if not raw_fifo.empty else 0.0
        raw_fifo_status = "Готово" if raw_diff <= 0.05 else ("Внимание" if raw_diff <= 1.0 else "Критично")
        rows.append(_quality_row(
            "FIFO", "Сверка сырья", raw_fifo_status,
            f"Суммарное расхождение физического сырья и слоёв: {raw_diff:.3f} м.",
            "Ничего делать не нужно." if raw_fifo_status == "Готово" else "Досинхронизировать FIFO-слои сырья после проверки физического остатка.",
            7,
        ))

    # Finished goods and sales FIFO. Small recent drift is neutral while WB APIs converge.
    fg_fifo = read_finished_goods_fifo_summary()
    if fg_fifo.empty:
        fg_status, fg_detail, fg_action = "Внимание", "Слои готовой продукции ещё не инициализированы.", "Инициализировать FIFO готовой продукции."
    else:
        guard = finished_goods_fifo_guard_status(
            str((sync_state or {}).get("finished_at") or (sync_state or {}).get("started_at") or "")
        )
        fg_status = str(guard.get("status", "Готово") or "Готово")
        fg_diff = int(guard.get("total_units", 0) or 0)
        cycles = int(guard.get("cycles", 0) or 0)
        age_minutes = float(guard.get("age_minutes", 0) or 0)
        stock_snapshot = str(guard.get("stock_snapshot_at", "") or "")[:19]
        last_fifo_event = str(guard.get("last_fifo_event_at", "") or "")[:19]
        guard_mode = str(guard.get("guard_mode", "") or "")
        if fg_status == "Готово":
            fg_detail = "Физические остатки и FIFO-слои совпадают."
            fg_action = "Ничего делать не нужно."
        elif fg_status == "Внимание" and guard_mode == "wb_incident_review":
            fg_detail = str(guard.get("reason", "Расхождение WB требует отдельной проверки."))
            fg_action = (
                "Открыть «Остатки → Сверка FIFO» и проверить блок «Контур WB / возможное внешнее выбытие». "
                "Обычную управленческую сверку WB не проводить."
            )
        elif fg_status == "Ожидание API":
            fg_detail = (
                f"Расхождение {fg_diff} ед.; контрольный цикл {cycles}, возраст {age_minutes:.0f} мин.; "
                f"снимок WB {stock_snapshot or '—'}, последняя FIFO-операция {last_fifo_event or '—'}."
            )
            wait_minutes = int(guard.get("wait_minutes", 0) or 0)
            fg_action = (
                f"Подождать ещё около {wait_minutes} мин. и выполнить полную перепроверку. "
                "Ручную сверку пока не проводить."
                if wait_minutes > 0 else
                "Выполнить полную перепроверку; ручную сверку проводить только если расхождение станет устойчивым."
            )
        else:
            fg_detail = (
                f"Устойчивое или крупное локальное расхождение {fg_diff} ед.; циклов {cycles}, возраст {age_minutes:.0f} мин.; "
                f"снимок WB {stock_snapshot or '—'}."
            )
            fg_action = "Открыть «Остатки → Сверка FIFO» и проверить безопасные локальные строки."
    rows.append(_quality_row("FIFO", "Готовая продукция", fg_status, fg_detail, fg_action, 8))

    sales_fifo = sales_fifo_tracking_status()
    if not sales_fifo.get("initialized"):
        sf_status, sf_action = "Внимание", "Инициализировать FIFO продаж с текущего момента."
    elif int(sales_fifo.get("errors", 0) or 0) > 0:
        sf_status, sf_action = "Критично", "Повторить ошибочные операции FIFO продаж."
    else:
        sf_status, sf_action = "Готово", "Ничего делать не нужно."
    rows.append(_quality_row(
        "FIFO", "Продажи и возвраты", sf_status,
        f"Списано продаж: {int(sales_fifo.get('sales_applied',0) or 0)}; возвратов: {int(sales_fifo.get('returns_applied',0) or 0)}; ошибок: {int(sales_fifo.get('errors',0) or 0)}.",
        sf_action, 8,
    ))

    # Local product pipeline completeness affects only production planning.
    if not enabled_prod.empty:
        prod_ids = set(pd.to_numeric(enabled_prod.get("nm_id"), errors="coerce").dropna().astype(int).tolist())
        pipeline_known_ids: set[int] = set()
        if not pipeline.empty and "nm_id" in pipeline.columns:
            known_mask = (
                pd.to_numeric(pipeline.get("local_known", 0), errors="coerce").fillna(0).astype(int).eq(1)
                & pd.to_numeric(pipeline.get("inbound_known", 0), errors="coerce").fillna(0).astype(int).eq(1)
            )
            pipeline_known_ids = set(pd.to_numeric(pipeline.loc[known_mask, "nm_id"], errors="coerce").dropna().astype(int).tolist())
        pipeline_coverage = len(prod_ids & pipeline_known_ids) / len(prod_ids) * 100 if prod_ids else 100.0
        pipe_status = "Готово" if pipeline_coverage >= 99.9 else ("Внимание" if pipeline_coverage >= 70 else "Критично")
        rows.append(_quality_row(
            "Производство", "Готово и в пути", pipe_status,
            f"Подтверждено {len(prod_ids & pipeline_known_ids)} из {len(prod_ids)} производимых карточек.",
            "Ничего делать не нужно." if pipe_status == "Готово" else "Подтвердить готовые остатки и поставки для недостающих карточек.",
            5,
        ))

    # Backups.
    backup_dt = None
    if backup_state:
        raw_backup_dt = backup_state.get("modified_at")
        if isinstance(raw_backup_dt, datetime):
            backup_dt = raw_backup_dt
            if backup_dt.tzinfo is None:
                backup_dt = backup_dt.replace(tzinfo=ZoneInfo("Europe/Moscow"))
        else:
            backup_dt = _parse_local_datetime(raw_backup_dt)
    if backup_dt is None:
        backup_status, backup_detail, backup_action = "Внимание", "Резервная копия не найдена.", "Создать ручную резервную копию."
    else:
        backup_age = max(0.0, (now_msk - backup_dt.astimezone(ZoneInfo("Europe/Moscow"))).total_seconds() / 3600)
        if backup_age <= 30:
            backup_status, backup_action = "Готово", "Ничего делать не нужно."
        elif backup_age <= 72:
            backup_status, backup_action = "Внимание", "Создать свежую резервную копию."
        else:
            backup_status, backup_action = "Критично", "Немедленно создать резервную копию."
        backup_detail = f"Последняя копия {backup_age:.1f} ч. назад."
    rows.append(_quality_row("Надёжность", "Резервная копия", backup_status, backup_detail, backup_action, 6))

    # Suppliers and purchase prices are optional until the procurement workflow is used.
    purchase_rules = app_settings.get("decision_purchase_rules", {}) if isinstance(app_settings, dict) else {}
    if not isinstance(purchase_rules, dict):
        purchase_rules = {}
    purchased_ids = active_ids - set(pd.to_numeric(enabled_prod.get("nm_id", pd.Series(dtype=float)), errors="coerce").dropna().astype(int).tolist()) if active_ids else set()
    configured_purchase_ids: set[int] = set()
    for key, rule in purchase_rules.items():
        if not isinstance(rule, dict):
            continue
        try:
            nm_key = int(str(key))
        except Exception:
            nm_key = 0
        if nm_key > 0 and str(rule.get("supplier_name", "") or "").strip() and float(rule.get("unit_cost_rub", 0) or 0) > 0:
            configured_purchase_ids.add(nm_key)
    supplier_coverage = len(purchased_ids & configured_purchase_ids) / len(purchased_ids) * 100 if purchased_ids else 100.0
    if purchased_ids and supplier_coverage >= 99.9:
        supplier_status = "Готово"
        supplier_action = "Ничего делать не нужно."
    else:
        supplier_status = "Отложено"
        supplier_action = "Заполнить позже, когда понадобится сводный план закупок."
    rows.append(_quality_row(
        "Закупки", "Поставщики, цены и MOQ", supplier_status,
        f"Настроено {len(purchased_ids & configured_purchase_ids)} из {len(purchased_ids)} закупаемых активных товаров ({supplier_coverage:.1f}%). Не влияет на финансы, FIFO и производство.",
        supplier_action, 0, required=False,
    ))

    quality = pd.DataFrame(rows)
    required_rows = quality[quality["Обязательная"]].copy()
    total_weight = float(required_rows["Вес"].sum())
    earned = float(required_rows["Баллы"].sum())
    score = earned / total_weight * 100 if total_weight > 0 else 0.0
    summary = {
        "score": score,
        "critical": int((required_rows["Статус"] == "Критично").sum()),
        "warnings": int((required_rows["Статус"] == "Внимание").sum()),
        "waiting": int((required_rows["Статус"] == "Ожидание API").sum()),
        "ready": int(required_rows["Статус"].isin(["Готово", "Ожидание API"]).sum()),
        "optional": int((quality["Статус"] == "Отложено").sum()),
    }

    def module_reliability(module_name: str, checks: list[str], note: str) -> dict[str, object]:
        subset = quality[quality["Проверка"].isin(checks)]
        statuses = set(subset["Статус"].astype(str))
        if "Критично" in statuses:
            status = "Ненадёжно"
        elif "Внимание" in statuses:
            status = "С ограничениями"
        elif subset.empty:
            status = "Не настроено"
        else:
            status = "Надёжно"
        return {"Расчётный контур": module_name, "Надёжность": status, "Основание": note}

    reliability = pd.DataFrame([
        module_reliability("Финансы и прибыль", ["Себестоимость активных товаров", "Финансовый отчёт WB", "Свежесть данных WB"], "Общая прибыль, маржа и расходы по артикулам."),
        module_reliability("Реклама", ["Свежесть рекламных расходов", "Свежесть данных WB"], "Рекомендации по ДРР и изменению ставок."),
        module_reliability("Производство", ["Производственные нормы", "Остатки сырья", "Сверка сырья", "Готово и в пути"], "Сменные задания, потребность в сырье и сроки пополнения WB."),
        module_reliability("FIFO и фактическая себестоимость", ["Сверка сырья", "Готовая продукция", "Продажи и возвраты"], "Стоимость партий, продаж и возвратов."),
        {"Расчётный контур": "Сводный план закупок", "Надёжность": "Отложено" if supplier_status == "Отложено" else "Надёжно", "Основание": "Поставщики и цены нужны только для формирования реальных заявок; остальные модули работают без них."},
    ])
    return quality, summary, reliability, cost_diagnostics



def _article_margin_signal(
    row: pd.Series,
    target_margin_pct: float,
    max_ad_share_pct: float,
    max_return_pct: float,
    low_stock_days: float,
) -> tuple[str, int, str, str]:
    """Operational signal based primarily on contribution margin, not allocated store overhead."""
    net_units = float(row.get("Продано нетто", 0) or 0)
    direct_profit = float(row.get("Маржинальная прибыль FIFO, ₽", 0) or 0)
    direct_margin = float(row.get("Маржинальность FIFO, %", 0) or 0)
    full_profit = float(row.get("Прибыль FIFO, ₽", 0) or 0)
    full_margin = float(row.get("Маржа FIFO, %", 0) or 0)
    ad_share = float(row.get("Доля рекламы, %", 0) or 0)
    ad_spend = float(row.get("Реклама", 0) or 0)
    return_rate = float(row.get("Возвраты, %", 0) or 0)
    source = str(row.get("Источник", "") or "")
    stock_days_raw = row.get("Запас, дней", math.nan)
    try:
        stock_days = float(stock_days_raw)
    except (TypeError, ValueError):
        stock_days = math.nan
    low_stock = math.isfinite(stock_days) and stock_days < low_stock_days
    recommended_ad_change = float(row.get("Рекомендованное изменение рекламы, ₽", 0) or 0)
    direct_break_even = float(row.get("Цена прямой безубыточности, ₽", 0) or 0)
    full_break_even = float(row.get("Цена полной безубыточности, ₽", 0) or 0)
    direct_target_price = float(row.get("Цена для целевой маржинальности, ₽", 0) or 0)

    if net_units <= 1:
        return (
            "Мало данных", 80,
            "Накопить минимум 2 продажи до изменения цены, закупки или рекламы",
            f"Продано нетто: {net_units:.0f} шт.",
        )
    if direct_profit < 0:
        if source == "Закупаемый товар":
            action = "Приостановить закупку и исправить прямую экономику карточки"
        else:
            action = "Не наращивать производство; исправить цену, расходы WB или себестоимость"
        if ad_spend > 0:
            action += "; рекламу сократить до безопасного уровня"
        price_note = ""
        if direct_break_even > 0:
            price_note = f"; прямая безубыточность ≈ {direct_break_even:,.0f} ₽".replace(",", " ")
        return (
            "Убыточный", 0, action,
            (f"Маржинальный убыток {abs(direct_profit):,.0f} ₽; маржинальность {direct_margin:.1f}%{price_note}").replace(",", " "),
        )
    if full_profit < 0:
        price_note = f"; полная безубыточность ≈ {full_break_even:,.0f} ₽".replace(",", " ") if full_break_even > 0 else ""
        return (
            "Не покрывает общие расходы", 5,
            "Не отключать карточку: повысить цену либо снизить долю общих расходов",
            (f"Прямая экономика положительная ({direct_profit:,.0f} ₽), но чистый результат {full_profit:,.0f} ₽{price_note}").replace(",", " "),
        )
    if low_stock:
        if source == "Закупаемый товар":
            action = "Сформировать закупку; рекламу не увеличивать до пополнения"
        else:
            action = "Ускорить производство или поставку; рекламу не увеличивать до пополнения"
        return (
            "Риск остатков", 10, action,
            f"Запас примерно на {stock_days:.1f} дня; положительный рекламный резерв заблокирован",
        )
    if direct_margin < 5:
        return (
            "Критическая маржа", 20,
            "Поднять цену или снизить прямые расходы карточки",
            f"Маржинальность только {direct_margin:.1f}% (чистая маржа {full_margin:.1f}%)",
        )
    if direct_margin < target_margin_pct:
        price_note = f"; ориентир цены ≈ {direct_target_price:,.0f} ₽".replace(",", " ") if direct_target_price > 0 else ""
        return (
            "Низкая маржа", 30,
            "Поднять цену или сократить прямой рекламный расход",
            f"Маржинальность {direct_margin:.1f}% ниже цели {target_margin_pct:.1f}%{price_note}",
        )
    if return_rate > max_return_pct:
        return (
            "Высокие возвраты", 40,
            "Проверить карточку, качество товара и причины возвратов",
            f"Возвраты {return_rate:.1f}% выше порога {max_return_pct:.1f}%",
        )
    if ad_share > max_ad_share_pct or recommended_ad_change < -0.01:
        reduce_by = max(0.0, -recommended_ad_change)
        return (
            "Перерасход рекламы", 50,
            "Снизить рекламный расход до расчётного лимита",
            (f"Доля рекламы {ad_share:.1f}%; сократить примерно на {reduce_by:,.0f} ₽".replace(",", " ")
             if reduce_by > 0 else f"Доля рекламы {ad_share:.1f}% выше порога {max_ad_share_pct:.1f}%"),
        )
    if direct_margin >= max(25.0, target_margin_pct + 5.0) and net_units >= 10:
        return (
            "Масштабировать", 60,
            "Допускается осторожное увеличение рекламы на 10% при сохранении достаточного запаса",
            f"Маржинальность {direct_margin:.1f}%; чистая прибыль {full_profit:,.0f} ₽".replace(",", " "),
        )
    return (
        "Стабильно", 70,
        "Сохранять текущую цену и рекламные настройки",
        f"Маржинальность {direct_margin:.1f}%, чистая маржа {full_margin:.1f}%",
    )


def _decision_center_recommendation(
    row: pd.Series,
    target_margin_pct: float,
    max_ad_share_pct: float,
    max_return_pct: float,
    low_stock_days: float,
    production_wb_lead_days: float = 10.0,
    purchase_target_days: float = 30.0,
    default_purchase_moq: int = 10,
    default_purchase_lead_days: float = 14.0,
    ad_step_pct: float = 25.0,
    ad_observation_days: int = 4,
) -> tuple[int, str, str, str, float, str]:
    """Return one prioritized action and a conservative ruble effect estimate."""
    net_units = max(0.0, float(row.get("Продано нетто", 0) or 0))
    sale_units = max(0.0, float(row.get("Продажи, шт", 0) or 0))
    direct_profit = float(row.get("Маржинальная прибыль FIFO, ₽", 0) or 0)
    direct_margin = float(row.get("Маржинальность FIFO, %", 0) or 0)
    full_profit = float(row.get("Прибыль FIFO, ₽", 0) or 0)
    direct_profit_unit = float(row.get("Маржинальная прибыль на ед., ₽", 0) or 0)
    revenue = abs(float(row.get("Выкупы по цене покупателя", 0) or 0))
    ad_spend = max(0.0, float(row.get("Реклама", 0) or 0))
    ad_change = float(row.get("Рекомендованное изменение рекламы, ₽", 0) or 0)
    return_rate = max(0.0, float(row.get("Возвраты, %", 0) or 0))
    sales_per_day = max(0.0, float(row.get("Продаж/день", 0) or 0))
    source = str(row.get("Источник", "") or "")
    current_price = max(0.0, float(row.get("Цена покупателя/ед., ₽", 0) or 0))
    direct_be = max(0.0, float(row.get("Цена прямой безубыточности, ₽", 0) or 0))
    full_be = max(0.0, float(row.get("Цена полной безубыточности, ₽", 0) or 0))
    direct_target = max(0.0, float(row.get("Цена для целевой маржинальности, ₽", 0) or 0))
    stock_raw = row.get("Запас, дней", math.nan)
    try:
        stock_days = float(stock_raw)
    except (TypeError, ValueError):
        stock_days = math.nan

    purchase_moq = max(1, int(float(row.get("MOQ закупки, шт", default_purchase_moq) or default_purchase_moq)))
    purchase_lead = max(0.0, float(row.get("Срок поставки закупки, дней", default_purchase_lead_days) or default_purchase_lead_days))
    purchase_target = max(float(low_stock_days), float(row.get("Целевой запас закупки, дней", purchase_target_days) or purchase_target_days))
    production_min_batch = max(1, int(float(row.get("Мин. партия производства, шт", 1) or 1)))

    def _price_action(target: float, level: str, forecast_margin: float) -> str:
        target = max(0.0, float(target or 0))
        if current_price <= 0 or target <= 0:
            return f"Проверить цену и вывести карточку на {level}"
        delta = target - current_price
        delta_pct = delta / current_price * 100 if current_price > 0 else 0.0
        if delta <= 0.5:
            return f"Проверить прямые расходы: текущая цена {money(current_price)} уже не ниже ориентира {money(target)}"
        warning = "; рост более 20% — сначала проверить себестоимость, контент и реакцию спроса" if delta_pct > 20.0 else ""
        return (
            f"Цена {money(current_price)} → ≈ {money(target)} (+{money(delta)}; +{delta_pct:.1f}%), "
            f"прогноз маржи ≈ {forecast_margin:.0f}%{warning}"
        )

    def _staged_ad_action(full_reduction: float) -> tuple[str, float]:
        full_reduction = min(ad_spend, max(0.0, float(full_reduction or 0)))
        step_share = min(1.0, max(0.05, float(ad_step_pct) / 100.0))
        first_step = min(full_reduction, ad_spend * step_share)
        if first_step <= 0:
            first_step = min(ad_spend, max(0.0, ad_spend * step_share))
        action = (
            f"Снизить ставки/бюджет на {float(ad_step_pct):.0f}% сейчас (≈ {money(first_step)}), "
            f"наблюдать {max(1, int(ad_observation_days))} дн."
        )
        if full_reduction > first_step + 0.5:
            action += f"; полный расчётный резерв сокращения {money(full_reduction)}"
        return action, first_step

    if net_units <= 1:
        return 90, "Низкий", "Данные", "Ничего не менять до накопления минимум 2 продаж", 0.0, "Недостаточно наблюдений"

    if direct_profit < 0:
        if ad_spend > 0 and ad_change < -0.01:
            full_reduction = min(ad_spend, max(0.0, -ad_change))
            action, first_step = _staged_ad_action(full_reduction)
            return 0, "Критический", "Реклама", action, first_step, "Эффект только первого безопасного шага сокращения рекламы"
        target = direct_be if direct_be > 0 else current_price
        action = _price_action(target, "прямую безубыточность", 0.0)
        if source == "Закупаемый товар":
            action += "; до исправления экономики не пополнять"
        return 1, "Критический", "Прямая экономика", action, abs(direct_profit), "Устранение текущего маржинального убытка при сохранении объёма"

    if full_profit < 0:
        target = full_be if full_be > 0 else current_price
        action = _price_action(target, "полную безубыточность", 0.0)
        action += "; это ориентир, карточку не отключать автоматически"
        return 5, "Высокий", "Полная экономика", action, abs(full_profit), "Покрытие распределённых общих расходов"

    # Replenishment horizon includes the time until stock becomes sellable plus the safety reserve.
    if source == "Закупаемый товар":
        horizon_days = max(purchase_target, purchase_lead + float(low_stock_days))
        needs_replenishment = math.isfinite(stock_days) and stock_days < horizon_days
        if needs_replenishment:
            gap_days = max(0.0, horizon_days - stock_days)
            raw_units = int(math.ceil(gap_days * sales_per_day)) if sales_per_day > 0 else purchase_moq
            replenish_units = max(purchase_moq, int(math.ceil(max(1, raw_units) / purchase_moq) * purchase_moq))
            protected_margin = replenish_units * max(0.0, direct_profit_unit)
            action = (
                f"Заказать {num(replenish_units)} ед. (MOQ {num(purchase_moq)}; срок поставки {purchase_lead:.0f} дн.; "
                f"целевой горизонт {horizon_days:.0f} дн.); рекламу не увеличивать до пополнения"
            )
            return 10, "Высокий", "Остатки", action, protected_margin, "Маржинальная прибыль объёма до целевого запаса с учётом срока поставки и MOQ"
    else:
        horizon_days = float(low_stock_days) + max(0.0, float(production_wb_lead_days))
        needs_replenishment = math.isfinite(stock_days) and stock_days < horizon_days
        if needs_replenishment:
            gap_days = max(0.0, horizon_days - stock_days)
            raw_units = int(math.ceil(gap_days * sales_per_day)) if sales_per_day > 0 else production_min_batch
            replenish_units = max(production_min_batch, int(math.ceil(max(1, raw_units) / production_min_batch) * production_min_batch))
            protected_margin = replenish_units * max(0.0, direct_profit_unit)
            action = (
                f"Произвести и отгрузить {num(replenish_units)} ед. (до WB {float(production_wb_lead_days):.0f} дн. + "
                f"страховой запас {float(low_stock_days):.0f} дн.; цель {horizon_days:.0f} дн.)"
            )
            return 10, "Высокий", "Остатки", action, protected_margin, "Маржинальная прибыль объёма, необходимого к моменту появления товара на WB"

    if ad_change < -0.01 or (ad_spend > 0 and float(row.get("Доля рекламы, %", 0) or 0) > float(max_ad_share_pct)):
        full_reduction = min(ad_spend, max(0.0, -ad_change))
        if full_reduction <= 0:
            full_reduction = max(0.0, ad_spend * 0.10)
        action, first_step = _staged_ad_action(full_reduction)
        return 20, "Высокий", "Реклама", action, first_step, "Эффект первого этапа; после периода наблюдения требуется повторный расчёт"

    if return_rate > float(max_return_pct):
        excess_returns = max(0.0, sale_units * (return_rate - float(max_return_pct)) / 100.0)
        effect = excess_returns * max(0.0, direct_profit_unit)
        return 30, "Средний", "Возвраты", "Разобрать причины возвратов и исправить карточку/качество", effect, "Маржинальная прибыль при снижении возвратов до установленного порога"

    if direct_margin < float(target_margin_pct):
        effect = max(0.0, revenue * float(target_margin_pct) / 100.0 - direct_profit)
        target = direct_target if direct_target > 0 else current_price
        action = _price_action(target, "целевую маржинальность", float(target_margin_pct))
        return 40, "Средний", "Цена", action, effect, "Разница до целевой маржинальной прибыли при сохранении объёма"

    if direct_margin >= max(25.0, float(target_margin_pct) + 5.0) and net_units >= 10:
        effect = max(0.0, direct_profit * 0.10)
        return 60, "Низкий", "Масштабирование", "Протестировать увеличение рекламы на 10% только при запасе выше полного горизонта пополнения", effect, "Оценка при росте продаж на 10% и сохранении текущей экономики"

    return 70, "Низкий", "Контроль", "Сохранить текущие настройки", 0.0, "Карточка проходит установленные пороги"

def build_article_margin_view(
    financial_products: pd.DataFrame,
    fifo_sales: pd.DataFrame,
    target_margin_pct: float = 15.0,
    max_ad_share_pct: float = 12.0,
    max_return_pct: float = 10.0,
    low_stock_days: float = 14.0,
    target_total_profit: float | None = None,
    production_wb_lead_days: float = 10.0,
    purchase_target_days: float = 30.0,
    default_purchase_moq: int = 10,
    default_purchase_lead_days: float = 14.0,
    ad_step_pct: float = 25.0,
    ad_observation_days: int = 4,
    purchase_rules: dict | None = None,
) -> pd.DataFrame:
    """Build reconciled per-SKU unit economics with FIFO and safe operational recommendations."""
    if financial_products is None or financial_products.empty:
        return pd.DataFrame()

    result = financial_products.copy()
    result["Артикул WB"] = pd.to_numeric(result.get("Артикул WB", 0), errors="coerce").fillna(0).astype(int)
    numeric_cols = [
        "Продажи, шт", "Возвраты, шт", "Продано нетто", "Возвраты, %",
        "Выкупы по цене покупателя", "Продажи по отчёту", "К перечислению за товар",
        "Прямые расходы WB", "Реклама", "Доля рекламы, %", "Себестоимость",
        "Маржинальная прибыль", "Распределено общих расходов", "Расчётная прибыль",
        "Остаток", "Продаж/день", "Запас, дней",
    ]
    for col in numeric_cols:
        if col not in result.columns:
            result[col] = math.nan if col == "Запас, дней" else 0.0
        result[col] = pd.to_numeric(result[col], errors="coerce")
        if col != "Запас, дней":
            result[col] = result[col].fillna(0.0)

    # Identify which cards are produced in-house. All other active cards are treated as purchased goods.
    own_nm_ids: set[int] = set()
    production_min_batches: dict[int, int] = {}
    try:
        production_cfg = read_table("production_settings")
        if not production_cfg.empty:
            production_cfg["nm_id"] = pd.to_numeric(production_cfg.get("nm_id", 0), errors="coerce").fillna(0).astype(int)
            production_cfg["enabled"] = pd.to_numeric(production_cfg.get("enabled", 0), errors="coerce").fillna(0).astype(int)
            production_cfg["min_batch"] = pd.to_numeric(production_cfg.get("min_batch", 1), errors="coerce").fillna(1).clip(lower=1).astype(int)
            enabled_production = production_cfg.loc[production_cfg["enabled"].eq(1)].copy()
            own_nm_ids = set(enabled_production["nm_id"].tolist())
            production_min_batches = dict(zip(enabled_production["nm_id"], enabled_production["min_batch"]))
    except Exception:
        own_nm_ids = set()
        production_min_batches = {}
    result["Источник"] = result["Артикул WB"].map(
        lambda nm_id: "Собственное производство" if int(nm_id) in own_nm_ids else "Закупаемый товар"
    )
    result["Мин. партия производства, шт"] = result["Артикул WB"].map(
        lambda nm_id: max(1, int(production_min_batches.get(int(nm_id), 1)))
    )

    purchase_rules = purchase_rules if isinstance(purchase_rules, dict) else {}
    def _purchase_rule(row: pd.Series) -> dict:
        nm_key = str(int(row.get("Артикул WB", 0) or 0))
        article_key = str(row.get("Артикул продавца", "") or "").strip()
        rule = purchase_rules.get(nm_key) or purchase_rules.get(article_key) or {}
        return rule if isinstance(rule, dict) else {}

    result["MOQ закупки, шт"] = result.apply(
        lambda row: max(1, int(float(_purchase_rule(row).get("moq", default_purchase_moq) or default_purchase_moq))), axis=1
    )
    result["Срок поставки закупки, дней"] = result.apply(
        lambda row: max(0.0, float(_purchase_rule(row).get("lead_days", default_purchase_lead_days) or default_purchase_lead_days)), axis=1
    )
    result["Целевой запас закупки, дней"] = result.apply(
        lambda row: max(float(low_stock_days), float(_purchase_rule(row).get("target_days", purchase_target_days) or purchase_target_days)), axis=1
    )
    result["Поставщик закупки"] = result.apply(
        lambda row: str(_purchase_rule(row).get("supplier_name", "") or "").strip(), axis=1
    )
    result["Цена закупки, ₽/шт"] = result.apply(
        lambda row: max(0.0, float(_purchase_rule(row).get("unit_cost_rub", 0) or 0)), axis=1
    )
    result.loc[result["Источник"].eq("Собственное производство"), [
        "MOQ закупки, шт", "Срок поставки закупки, дней", "Целевой запас закупки, дней",
        "Поставщик закупки", "Цена закупки, ₽/шт"
    ]] = [0, 0.0, 0.0, "", 0.0]

    fifo_cols = [
        "nm_id", "estimated_fifo_cogs_rub", "exact_fifo_cogs_rub",
        "covered_events", "total_events", "error_events",
    ]
    if fifo_sales is not None and not fifo_sales.empty:
        fifo = fifo_sales.copy()
        for col in fifo_cols:
            if col not in fifo.columns:
                fifo[col] = 0
        fifo = fifo[fifo_cols].copy()
        fifo["nm_id"] = pd.to_numeric(fifo["nm_id"], errors="coerce").fillna(0).astype(int)
        for col in fifo_cols[1:]:
            fifo[col] = pd.to_numeric(fifo[col], errors="coerce").fillna(0.0)
        fifo = fifo.groupby("nm_id", as_index=False).sum(numeric_only=True)
        result = result.merge(fifo, left_on="Артикул WB", right_on="nm_id", how="left")
    else:
        for col in fifo_cols:
            result[col] = 0

    for col in fifo_cols[1:]:
        result[col] = pd.to_numeric(result.get(col, 0), errors="coerce").fillna(0.0)
    result["FIFO COGS, ₽"] = result["estimated_fifo_cogs_rub"].where(
        result["total_events"] > 0, result["Себестоимость"]
    )
    result["Точная FIFO-часть, ₽"] = result["exact_fifo_cogs_rub"]
    result["Покрытие FIFO, %"] = result.apply(
        lambda row: float(row["covered_events"]) / float(row["total_events"]) * 100
        if float(row["total_events"]) > 0 else 0.0, axis=1
    )
    result["Изменение COGS, ₽"] = result["FIFO COGS, ₽"] - result["Себестоимость"]
    result["Маржинальная прибыль FIFO, ₽"] = result["Маржинальная прибыль"] - result["Изменение COGS, ₽"]
    result["Прибыль до магазинной сверки, ₽"] = result["Расчётная прибыль"] - result["Изменение COGS, ₽"]

    # Reconcile the sum of active cards with the store result. The financial module intentionally
    # leaves deductions, technical rows and other store-level effects outside active cards.
    # Here they are allocated transparently so that per-SKU profit adds up exactly to the dashboard total.
    result["Распределено магазинной разницы, ₽"] = 0.0
    if target_total_profit is not None and len(result) > 0:
        target_total_profit = float(target_total_profit)
        base_total = float(result["Прибыль до магазинной сверки, ₽"].sum())
        store_gap = target_total_profit - base_total
        basis = result["Выкупы по цене покупателя"].abs()
        if float(basis.sum()) <= 0:
            basis = result["Продано нетто"].clip(lower=0)
        if float(basis.sum()) > 0:
            result["Распределено магазинной разницы, ₽"] = basis / float(basis.sum()) * store_gap
            # Remove floating point tail from the largest row to guarantee exact reconciliation.
            tail = target_total_profit - float((result["Прибыль до магазинной сверки, ₽"] + result["Распределено магазинной разницы, ₽"]).sum())
            if abs(tail) > 1e-8:
                idx = basis.idxmax()
                result.at[idx, "Распределено магазинной разницы, ₽"] += tail
    result["Прибыль FIFO, ₽"] = result["Прибыль до магазинной сверки, ₽"] + result["Распределено магазинной разницы, ₽"]

    revenue_abs = result["Выкупы по цене покупателя"].abs()
    net_units = result["Продано нетто"].clip(lower=0)
    # Two profit levels: contribution economics for operational decisions and full profit for store reconciliation.
    result["Маржинальность FIFO, %"] = (
        result["Маржинальная прибыль FIFO, ₽"] / revenue_abs * 100
    ).where(revenue_abs > 0, 0.0).fillna(0.0)
    result["Маржинальная прибыль на ед., ₽"] = (
        result["Маржинальная прибыль FIFO, ₽"] / net_units
    ).where(net_units > 0, 0.0).fillna(0.0)
    result["Маржа FIFO, %"] = (result["Прибыль FIFO, ₽"] / revenue_abs * 100).where(revenue_abs > 0, 0.0).fillna(0.0)
    result["Прибыль на ед., ₽"] = (result["Прибыль FIFO, ₽"] / net_units).where(net_units > 0, 0.0).fillna(0.0)
    result["Чистая прибыль FIFO, ₽"] = result["Прибыль FIFO, ₽"]
    result["Чистая маржа FIFO, %"] = result["Маржа FIFO, %"]
    result["Чистая прибыль на ед., ₽"] = result["Прибыль на ед., ₽"]

    per_unit_map = {
        "Цена покупателя/ед., ₽": "Выкупы по цене покупателя",
        "К перечислению/ед., ₽": "К перечислению за товар",
        "Расходы WB/ед., ₽": "Прямые расходы WB",
        "Реклама/ед., ₽": "Реклама",
        "FIFO COGS/ед., ₽": "FIFO COGS, ₽",
        "Общие расходы/ед., ₽": "Распределено общих расходов",
        "Магазинная корректировка/ед., ₽": "Распределено магазинной разницы, ₽",
    }
    for target, source in per_unit_map.items():
        result[target] = (result[source] / net_units).where(net_units > 0, 0.0).fillna(0.0)

    result["Все расходы, ₽"] = (
        result["Прямые расходы WB"] + result["Реклама"] + result["FIFO COGS, ₽"]
        + result["Распределено общих расходов"]
        + (-result["Распределено магазинной разницы, ₽"]).clip(lower=0.0)
    )
    result["ROI FIFO, %"] = (
        result["Прибыль FIFO, ₽"] / result["Все расходы, ₽"] * 100
    ).where(result["Все расходы, ₽"] > 0, 0.0).fillna(0.0)

    # Estimated retail prices. The payout ratio is assumed stable; volumes and fixed per-period costs are unchanged.
    result["Коэффициент перечисления"] = (
        result["К перечислению за товар"] / revenue_abs
    ).where(revenue_abs > 0, 0.0).replace([math.inf, -math.inf], 0.0).fillna(0.0)
    result["Коэффициент перечисления"] = result["Коэффициент перечисления"].clip(lower=0.0, upper=1.5)
    price_columns = [
        "Цена прямой безубыточности, ₽",
        "Цена полной безубыточности, ₽",
        "Цена для целевой маржинальности, ₽",
        "Цена для целевой чистой маржи, ₽",
    ]
    for col in price_columns:
        result[col] = 0.0

    def _price_for_profit(current_revenue: float, units: float, payout_ratio: float, profit: float, target_rate: float = 0.0) -> float:
        if units <= 0 or current_revenue <= 0 or payout_ratio <= 0:
            return 0.0
        if target_rate <= 0:
            total = current_revenue - profit / payout_ratio
        elif payout_ratio > target_rate + 1e-9:
            total = (payout_ratio * current_revenue - profit) / (payout_ratio - target_rate)
        else:
            return 0.0
        return max(0.0, total / units)

    target_rate = float(target_margin_pct) / 100.0
    for idx, row in result.iterrows():
        units = float(row.get("Продано нетто", 0) or 0)
        revenue = abs(float(row.get("Выкупы по цене покупателя", 0) or 0))
        payout_ratio = float(row.get("Коэффициент перечисления", 0) or 0)
        if units <= 0 or revenue <= 0 or payout_ratio <= 0:
            continue
        current_price = revenue / units
        direct_profit = float(row.get("Маржинальная прибыль FIFO, ₽", 0) or 0)
        full_profit = float(row.get("Прибыль FIFO, ₽", 0) or 0)
        direct_be = _price_for_profit(revenue, units, payout_ratio, direct_profit, 0.0)
        full_be = _price_for_profit(revenue, units, payout_ratio, full_profit, 0.0)
        direct_target = _price_for_profit(revenue, units, payout_ratio, direct_profit, target_rate)
        full_target = _price_for_profit(revenue, units, payout_ratio, full_profit, target_rate)
        result.at[idx, "Цена прямой безубыточности, ₽"] = direct_be
        result.at[idx, "Цена полной безубыточности, ₽"] = full_be
        result.at[idx, "Цена для целевой маржинальности, ₽"] = max(current_price if direct_profit >= revenue * target_rate else 0.0, direct_target)
        result.at[idx, "Цена для целевой чистой маржи, ₽"] = max(current_price if full_profit >= revenue * target_rate else 0.0, full_target)

    # Backward-compatible aliases used by older exports and UI code.
    result["Цена безубыточности, ₽"] = result["Цена полной безубыточности, ₽"]
    result["Цена для целевой маржи, ₽"] = result["Цена для целевой чистой маржи, ₽"]
    result["Изменение цены до прямой безубыточности, %"] = (
        (result["Цена прямой безубыточности, ₽"] / result["Цена покупателя/ед., ₽"] - 1.0) * 100
    ).where(result["Цена покупателя/ед., ₽"] > 0, 0.0).fillna(0.0)
    result["Изменение цены до полной безубыточности, %"] = (
        (result["Цена полной безубыточности, ₽"] / result["Цена покупателя/ед., ₽"] - 1.0) * 100
    ).where(result["Цена покупателя/ед., ₽"] > 0, 0.0).fillna(0.0)
    result["Изменение цены до безубыточности, %"] = result["Изменение цены до полной безубыточности, %"]
    result["Изменение цены до цели, %"] = (
        (result["Цена для целевой чистой маржи, ₽"] / result["Цена покупателя/ед., ₽"] - 1.0) * 100
    ).where(result["Цена покупателя/ед., ₽"] > 0, 0.0).fillna(0.0)

    target_profit = revenue_abs * float(target_margin_pct) / 100.0
    # Advertising decisions use contribution economics; allocated store overhead must not distort campaign limits.
    result["Рекламный лимит, ₽"] = (
        result["Реклама"] + result["Маржинальная прибыль FIFO, ₽"] - target_profit
    ).clip(lower=0.0)
    result["Теоретический рекламный резерв, ₽"] = result["Рекламный лимит, ₽"] - result["Реклама"]

    # Safe recommendation: never increase advertising when stock is below the threshold.
    # An unprofitable card can only be recommended to reduce existing ad spend, never to increase it.
    def _recommended_ad_change(row: pd.Series) -> float:
        theoretical = float(row.get("Теоретический рекламный резерв, ₽", 0) or 0)
        current_ad = max(0.0, float(row.get("Реклама", 0) or 0))
        profit = float(row.get("Маржинальная прибыль FIFO, ₽", 0) or 0)
        stock_raw = row.get("Запас, дней", math.nan)
        try:
            stock_days = float(stock_raw)
        except (TypeError, ValueError):
            stock_days = math.nan
        low_stock = math.isfinite(stock_days) and stock_days < float(low_stock_days)
        if profit < 0:
            return min(0.0, theoretical) if current_ad > 0 else 0.0
        if low_stock:
            return min(0.0, theoretical)
        if theoretical < 0:
            return theoretical
        margin = float(row.get("Маржинальность FIFO, %", 0) or 0)
        if margin >= max(25.0, float(target_margin_pct) + 5.0):
            return theoretical
        return 0.0

    result["Рекомендованное изменение рекламы, ₽"] = result.apply(_recommended_ad_change, axis=1)
    # Compatibility alias for older UI/export code.
    result["Коррекция рекламы, ₽"] = result["Рекомендованное изменение рекламы, ₽"]

    result["Решение по закупке"] = "—"
    purchased = result["Источник"].eq("Закупаемый товар")
    result.loc[purchased & (result["Маржинальная прибыль FIFO, ₽"] < 0), "Решение по закупке"] = "Пауза до выхода в плюс"
    result.loc[purchased & (result["Маржинальная прибыль FIFO, ₽"] >= 0) & (result["Запас, дней"] < float(low_stock_days)), "Решение по закупке"] = "Заказать пополнение"
    result.loc[purchased & (result["Маржинальная прибыль FIFO, ₽"] >= 0) & ~(result["Запас, дней"] < float(low_stock_days)), "Решение по закупке"] = "По плану"

    signals = result.apply(
        lambda row: _article_margin_signal(
            row, target_margin_pct, max_ad_share_pct, max_return_pct, low_stock_days
        ), axis=1
    )
    result["Сигнал"] = signals.map(lambda value: value[0])
    result["_priority"] = signals.map(lambda value: value[1])
    result["Действие"] = signals.map(lambda value: value[2])
    result["Причина"] = signals.map(lambda value: value[3])

    decisions = result.apply(
        lambda row: _decision_center_recommendation(
            row, target_margin_pct, max_ad_share_pct, max_return_pct, low_stock_days,
            production_wb_lead_days=production_wb_lead_days,
            purchase_target_days=purchase_target_days,
            default_purchase_moq=default_purchase_moq,
            default_purchase_lead_days=default_purchase_lead_days,
            ad_step_pct=ad_step_pct,
            ad_observation_days=ad_observation_days,
        ), axis=1
    )
    result["_decision_priority"] = decisions.map(lambda value: value[0])
    result["Приоритет решения"] = decisions.map(lambda value: value[1])
    result["Фокус решения"] = decisions.map(lambda value: value[2])
    result["Решение сейчас"] = decisions.map(lambda value: value[3])
    result["Ожидаемый эффект, ₽"] = decisions.map(lambda value: value[4])
    result["Основание эффекта"] = decisions.map(lambda value: value[5])
    result = result.sort_values(["_decision_priority", "Ожидаемый эффект, ₽", "Прибыль FIFO, ₽"], ascending=[True, False, True]).reset_index(drop=True)
    return result

def procurement_recommendations(products: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build current raw-material and purchased-product replenishment suggestions."""
    if products is None or products.empty:
        return pd.DataFrame(), pd.DataFrame()
    capacity = get_production_capacity()
    emergency_days = max(1, int(capacity.get("emergency_cover_days", 14) or 14))
    production_cfg = read_table("production_settings")
    pipeline = read_table("product_pipeline")
    raw_inventory = read_table("material_inventory_color")

    if production_cfg.empty:
        enabled_cfg = pd.DataFrame()
    else:
        cfg = production_cfg.copy()
        cfg["nm_id"] = pd.to_numeric(cfg["nm_id"], errors="coerce").fillna(0).astype(int)
        cfg["enabled"] = pd.to_numeric(cfg.get("enabled", 0), errors="coerce").fillna(0).astype(int)
        for col, default in [("material_per_unit", 0.0), ("target_days", 21), ("min_batch", 1)]:
            cfg[col] = pd.to_numeric(cfg.get(col, default), errors="coerce").fillna(default)
        cfg["material_name"] = cfg.get("material_name", "").fillna("").astype(str).str.strip()
        enabled_cfg = cfg[cfg["enabled"].eq(1)].copy()
    cfg_lookup = enabled_cfg.set_index("nm_id").to_dict("index") if not enabled_cfg.empty else {}
    own_nm = set(cfg_lookup)

    pipeline_map: dict[int, dict] = {}
    if not pipeline.empty:
        pipe = pipeline.copy()
        pipe["nm_id"] = pd.to_numeric(pipe["nm_id"], errors="coerce").fillna(0).astype(int)
        pipeline_map = {int(row["nm_id"]): row.to_dict() for _, row in pipe.iterrows()}

    material_rows: list[dict] = []
    purchase_rows: list[dict] = []
    for _, row in products.iterrows():
        try:
            nm_id = int(float(row.get("Артикул WB", 0) or 0))
        except (TypeError, ValueError):
            nm_id = 0
        if nm_id <= 0:
            continue
        article = str(row.get("Артикул продавца", "") or "")
        product_name = str(row.get("Товар", "") or "")
        stock = max(0.0, float(row.get("Остаток", 0) or 0))
        daily = max(0.0, float(row.get("Продаж/день", 0) or 0))
        try:
            stock_days = float(row.get("Запас, дней", math.nan))
        except (TypeError, ValueError):
            stock_days = math.nan
        pipe = pipeline_map.get(nm_id, {})
        ready = max(0, int(float(pipe.get("ready_units", 0) or 0))) if int(pipe.get("local_known", 0) or 0) else 0
        inbound = max(0, int(float(pipe.get("inbound_units", 0) or 0))) if int(pipe.get("inbound_known", 0) or 0) else 0
        if nm_id in own_nm:
            cfg_row = cfg_lookup[nm_id]
            target_days = max(1, int(float(cfg_row.get("target_days", 21) or 21)))
            min_batch = max(1, int(float(cfg_row.get("min_batch", 1) or 1)))
            rate = max(0.0, float(cfg_row.get("material_per_unit", 0) or 0))
            need_units = ceil_to_batch(max(daily * target_days - stock - ready - inbound, 0.0), min_batch)
            material_name = str(cfg_row.get("material_name", "") or "").strip()
            if need_units > 0 and material_name and rate > 0:
                material_rows.append({
                    "Материал / цвет": material_name, "Артикул продавца": article,
                    "Нужно материала, м": need_units * rate,
                })
        elif pd.notna(stock_days) and stock_days <= emergency_days:
            target_days = max(21, emergency_days)
            qty = int(math.ceil(max(daily * target_days - stock - inbound, 0.0)))
            if qty > 0:
                purchase_rows.append({
                    "Артикул WB": nm_id, "Артикул продавца": article, "Товар": product_name,
                    "Остаток WB": int(round(stock)), "Продаж/день": daily,
                    "Запас WB, дней": stock_days, "В пути": inbound,
                    "Ориентировочно заказать, шт.": qty,
                })

    material_df = pd.DataFrame()
    if material_rows:
        material_df = pd.DataFrame(material_rows).groupby("Материал / цвет", as_index=False).agg({
            "Нужно материала, м": "sum",
            "Артикул продавца": lambda values: ", ".join(sorted(set(str(v) for v in values if str(v).strip()))),
        })
        material_df["material_key"] = material_df["Материал / цвет"].apply(material_key)
        if not raw_inventory.empty:
            inv = raw_inventory[[
                "material_key", "balance_known", "full_rolls", "partial_meters", "roll_length", "unit", "tracking_mode"
            ]].copy()
            material_df = material_df.merge(inv, on="material_key", how="left")
        for col, default in [
            ("balance_known", 0), ("full_rolls", 0), ("partial_meters", 0.0), ("roll_length", 25.5),
            ("unit", "м"), ("tracking_mode", "packaged"),
        ]:
            if col not in material_df.columns:
                material_df[col] = default
            if col in {"unit", "tracking_mode"}:
                material_df[col] = material_df[col].fillna(default).astype(str)
                material_df.loc[material_df[col].str.strip().eq(""), col] = default
            else:
                material_df[col] = pd.to_numeric(material_df[col], errors="coerce").fillna(default)
        material_df["На складе, м"] = material_df["full_rolls"] * material_df["roll_length"] + material_df["partial_meters"]
        material_df["Не хватает, м"] = (material_df["Нужно материала, м"] - material_df["На складе, м"]).clip(lower=0)
        material_df["Упаковок докупить"] = material_df.apply(
            lambda r: packages_to_buy(r["Не хватает, м"], r["roll_length"], int(r["balance_known"]) == 1), axis=1
        )
        material_df = material_df[(material_df["balance_known"].eq(1)) & (material_df["Не хватает, м"] > 0.01)].copy()
    return material_df, pd.DataFrame(purchase_rows)


def build_consolidated_purchase_plan(
    products: pd.DataFrame,
    financial_products: pd.DataFrame,
    procurement_orders: pd.DataFrame,
    procurement_items: pd.DataFrame,
    suppliers: pd.DataFrame,
    purchase_rules: dict,
    safety_days: float = 14.0,
    default_target_days: float = 30.0,
    default_moq: int = 10,
    default_lead_days: float = 14.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build a supplier-consolidated plan for profitable purchased SKUs."""
    item_columns = [
        "Приоритет", "Статус плана", "Поставщик", "Артикул WB", "Артикул продавца", "Товар",
        "Остаток WB", "Готово у вас", "В пути", "Открытые закупки", "Продаж/день", "Запас WB, дней",
        "MOQ", "Срок поставки, дней", "Целевой горизонт, дней", "Рекомендовано, шт",
        "Полная цена, ₽/шт", "Сумма, ₽", "Маржинальная прибыль/ед., ₽",
        "Сохраняемая маржинальная прибыль, ₽", "Оплатить до", "Ожидаемая поставка", "Источник цены", "Причина"
    ]
    summary_columns = [
        "Поставщик", "Позиций", "Единиц", "Сумма заказа, ₽", "Сохраняемая маржинальная прибыль, ₽",
        "Оплатить до", "Ожидаемая поставка", "Нулевых цен", "Критических позиций", "Товары"
    ]
    if products is None or products.empty:
        return pd.DataFrame(columns=item_columns), pd.DataFrame(columns=summary_columns)

    rules = purchase_rules if isinstance(purchase_rules, dict) else {}
    safety_days = max(1.0, float(safety_days or 14.0))
    default_target_days = max(safety_days, float(default_target_days or 30.0))
    default_moq = max(1, int(default_moq or 10))
    default_lead_days = max(0.0, float(default_lead_days or 14.0))
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()

    own_nm_ids: set[int] = set()
    try:
        production_cfg = read_table("production_settings")
        if not production_cfg.empty:
            production_cfg["nm_id"] = pd.to_numeric(production_cfg.get("nm_id", 0), errors="coerce").fillna(0).astype(int)
            production_cfg["enabled"] = pd.to_numeric(production_cfg.get("enabled", 0), errors="coerce").fillna(0).astype(int)
            own_nm_ids = set(production_cfg.loc[production_cfg["enabled"].eq(1), "nm_id"].tolist())
    except Exception:
        own_nm_ids = set()

    pipeline_map: dict[int, dict] = {}
    try:
        pipeline = read_table("product_pipeline")
        if not pipeline.empty:
            pipeline["nm_id"] = pd.to_numeric(pipeline.get("nm_id", 0), errors="coerce").fillna(0).astype(int)
            pipeline_map = {int(r["nm_id"]): r.to_dict() for _, r in pipeline.iterrows()}
    except Exception:
        pipeline_map = {}

    fin_map: dict[int, dict] = {}
    if financial_products is not None and not financial_products.empty:
        fin = financial_products.copy()
        fin["Артикул WB"] = pd.to_numeric(fin.get("Артикул WB", 0), errors="coerce").fillna(0).astype(int)
        fin_map = {int(r["Артикул WB"]): r.to_dict() for _, r in fin.iterrows()}

    supplier_lookup: dict[str, dict] = {}
    if suppliers is not None and not suppliers.empty:
        for _, supplier in suppliers.iterrows():
            name = str(supplier.get("name", "") or "").strip()
            if name:
                supplier_lookup[name.casefold()] = supplier.to_dict()

    latest_context: dict[int, dict] = {}
    open_qty: dict[int, float] = {}
    if procurement_items is not None and not procurement_items.empty:
        items = procurement_items.copy()
        items["nm_id"] = pd.to_numeric(items.get("nm_id", 0), errors="coerce").fillna(0).astype(int)
        items["quantity"] = pd.to_numeric(items.get("quantity", 0), errors="coerce").fillna(0.0)
        items["posted_quantity"] = pd.to_numeric(items.get("posted_quantity", 0), errors="coerce").fillna(0.0)
        items["unit_price"] = pd.to_numeric(items.get("unit_price", 0), errors="coerce").fillna(0.0)
        if procurement_orders is not None and not procurement_orders.empty:
            orders = procurement_orders.copy()
            orders["id"] = pd.to_numeric(orders.get("id", 0), errors="coerce").fillna(0).astype(int)
            order_cols = [c for c in ["id", "supplier_name", "supplier_id", "status", "order_date", "payment_terms_days", "lead_time_days"] if c in orders.columns]
            items = items.merge(orders[order_cols].rename(columns={"id": "order_id"}), on="order_id", how="left", suffixes=("", "_order"))
            items["order_date_sort"] = pd.to_datetime(items.get("order_date"), errors="coerce")
            latest = items[items["nm_id"].gt(0)].sort_values(["order_date_sort", "id"], ascending=[False, False])
            for nm_id, group in latest.groupby("nm_id", sort=False):
                row = group.iloc[0]
                latest_context[int(nm_id)] = {
                    "supplier_name": str(row.get("supplier_name", "") or "").strip(),
                    "unit_price": max(0.0, float(row.get("unit_price", 0) or 0)),
                    "lead_time_days": max(0.0, float(row.get("lead_time_days", 0) or 0)),
                    "payment_terms_days": max(0, int(float(row.get("payment_terms_days", 0) or 0))),
                }
            active_statuses = {"Запланировано", "Заказано", "Частично оплачено", "Оплачено", "В пути", "Частично получено"}
            active_items = items[items.get("status", "").astype(str).isin(active_statuses)].copy()
            if not active_items.empty:
                active_items["remaining"] = (active_items["quantity"] - active_items["posted_quantity"]).clip(lower=0.0)
                open_qty = active_items.groupby("nm_id")["remaining"].sum().to_dict()

    rows: list[dict] = []
    for _, product in products.iterrows():
        try:
            nm_id = int(float(product.get("Артикул WB", 0) or 0))
        except (TypeError, ValueError):
            nm_id = 0
        if nm_id <= 0 or nm_id in own_nm_ids:
            continue
        article = str(product.get("Артикул продавца", "") or "").strip()
        product_name = str(product.get("Товар", "") or "").strip()
        rule = rules.get(str(nm_id)) or rules.get(article) or {}
        if not isinstance(rule, dict):
            rule = {}
        historical = latest_context.get(nm_id, {})
        supplier_name = str(rule.get("supplier_name", "") or historical.get("supplier_name", "") or "").strip()
        supplier_info = supplier_lookup.get(supplier_name.casefold(), {}) if supplier_name else {}
        supplier_label = supplier_name or "Не назначен"
        moq = max(1, int(float(rule.get("moq", default_moq) or default_moq)))
        lead_default = supplier_info.get("lead_time_days", historical.get("lead_time_days", default_lead_days))
        lead_days = max(0.0, float(rule.get("lead_days", lead_default) or default_lead_days))
        target_days = max(safety_days, float(rule.get("target_days", default_target_days) or default_target_days))
        horizon_days = max(target_days, lead_days + safety_days)
        payment_terms = max(0, int(float(supplier_info.get("payment_terms_days", historical.get("payment_terms_days", 0)) or 0)))

        stock = max(0.0, float(product.get("Остаток", product.get("Остаток WB", 0)) or 0))
        daily = max(0.0, float(product.get("Продаж/день", 0) or 0))
        stock_days_raw = product.get("Запас, дней", product.get("Запас WB, дней", math.nan))
        try:
            stock_days = float(stock_days_raw)
        except (TypeError, ValueError):
            stock_days = stock / daily if daily > 0 else math.inf
        pipeline = pipeline_map.get(nm_id, {})
        ready = max(0.0, float(pipeline.get("ready_units", 0) or 0)) if int(float(pipeline.get("local_known", 0) or 0)) == 1 else 0.0
        inbound = max(0.0, float(pipeline.get("inbound_units", 0) or 0)) if int(float(pipeline.get("inbound_known", 0) or 0)) == 1 else 0.0
        already_ordered = max(0.0, float(open_qty.get(nm_id, 0) or 0))

        fin_row = fin_map.get(nm_id, {})
        net_units = max(0.0, float(fin_row.get("Продано нетто", 0) or 0))
        direct_profit = float(fin_row.get("Маржинальная прибыль", fin_row.get("Расчётная прибыль", 0)) or 0)
        direct_profit_unit = direct_profit / net_units if net_units > 0 else 0.0
        base_cost_unit = max(0.0, float(fin_row.get("Себестоимость", 0) or 0) / net_units) if net_units > 0 else 0.0

        raw_need = max(0.0, daily * horizon_days - stock - ready - inbound - already_ordered)
        recommended = int(math.ceil(raw_need / moq) * moq) if raw_need > 0 else 0
        unit_cost = max(0.0, float(rule.get("unit_cost_rub", 0) or 0))
        price_source = "Правило SKU"
        if unit_cost <= 0:
            unit_cost = max(0.0, float(historical.get("unit_price", 0) or 0))
            price_source = "Последняя закупка"
        if unit_cost <= 0:
            unit_cost = base_cost_unit
            price_source = "Базовая себестоимость"
        if unit_cost <= 0:
            price_source = "Цена не указана"
        verified_purchase_price = price_source in {"Правило SKU", "Последняя закупка"}

        status = "К заказу"
        reason = f"Горизонт {horizon_days:.0f} дн. = срок {lead_days:.0f} + страховой запас {safety_days:.0f} дн."
        if direct_profit < -0.01:
            status = "Пауза: убыточно"
            recommended = 0
            reason = "Маржинальная прибыль карточки отрицательная — сначала исправить цену, расходы или себестоимость."
        elif recommended <= 0:
            status = "Покрыто"
            reason = "Остаток, товар в пути и открытые закупки покрывают целевой горизонт."
        elif not supplier_name:
            status = "Назначить поставщика"
            reason = "Потребность есть, но поставщик для SKU не назначен."
        elif not verified_purchase_price:
            status = "Проверить цену"
            reason = "Для оценки использована базовая себестоимость, а не подтверждённая полная цена поставщика. Уточните цену до создания заявки."
        elif unit_cost <= 0:
            status = "Указать цену"
            reason = "Нет полной цены единицы для расчёта суммы заявки."

        priority = "Критический" if stock_days <= lead_days else ("Высокий" if stock_days < horizon_days else "Средний")
        amount = recommended * unit_cost
        contribution = recommended * max(0.0, direct_profit_unit)
        rows.append({
            "Приоритет": priority, "Статус плана": status, "Поставщик": supplier_label,
            "Артикул WB": nm_id, "Артикул продавца": article, "Товар": product_name,
            "Остаток WB": stock, "Готово у вас": ready, "В пути": inbound,
            "Открытые закупки": already_ordered, "Продаж/день": daily,
            "Запас WB, дней": stock_days if math.isfinite(stock_days) else math.nan,
            "MOQ": moq, "Срок поставки, дней": lead_days, "Целевой горизонт, дней": horizon_days,
            "Рекомендовано, шт": recommended, "Полная цена, ₽/шт": unit_cost,
            "Сумма, ₽": amount, "Маржинальная прибыль/ед., ₽": direct_profit_unit,
            "Сохраняемая маржинальная прибыль, ₽": contribution,
            "Оплатить до": today + timedelta(days=payment_terms),
            "Ожидаемая поставка": today + timedelta(days=int(math.ceil(lead_days))),
            "Источник цены": price_source, "Причина": reason,
        })

    item_plan = pd.DataFrame(rows, columns=item_columns)
    if item_plan.empty:
        return item_plan, pd.DataFrame(columns=summary_columns)
    rank = {"Критический": 0, "Высокий": 1, "Средний": 2, "Низкий": 3}
    item_plan["_rank"] = item_plan["Приоритет"].map(rank).fillna(9)
    item_plan = item_plan.sort_values(["_rank", "Статус плана", "Сохраняемая маржинальная прибыль, ₽"], ascending=[True, True, False]).drop(columns=["_rank"]).reset_index(drop=True)

    orderable = item_plan[item_plan["Статус плана"].eq("К заказу") & item_plan["Рекомендовано, шт"].gt(0)].copy()
    if orderable.empty:
        return item_plan, pd.DataFrame(columns=summary_columns)
    summary = orderable.groupby("Поставщик", as_index=False).agg({
        "Артикул WB": "count", "Рекомендовано, шт": "sum", "Сумма, ₽": "sum",
        "Сохраняемая маржинальная прибыль, ₽": "sum", "Оплатить до": "min",
        "Ожидаемая поставка": "max",
        "Полная цена, ₽/шт": lambda v: int((pd.to_numeric(v, errors="coerce").fillna(0) <= 0).sum()),
        "Приоритет": lambda v: int((pd.Series(v).astype(str) == "Критический").sum()),
        "Артикул продавца": lambda v: ", ".join(sorted(set(str(x) for x in v if str(x).strip()))),
    }).rename(columns={
        "Артикул WB": "Позиций", "Рекомендовано, шт": "Единиц", "Сумма, ₽": "Сумма заказа, ₽",
        "Полная цена, ₽/шт": "Нулевых цен", "Приоритет": "Критических позиций", "Артикул продавца": "Товары",
    })
    summary = summary[summary_columns].sort_values(["Критических позиций", "Сумма заказа, ₽"], ascending=[False, False]).reset_index(drop=True)
    return item_plan, summary


