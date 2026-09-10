"""Pure computation for the "Контроль" (Data quality control) screen.

Same role as today_service.py / overview_service.py / ads_service.py: it wraps
exactly what pages/control.py renders -- ui_helpers.build_data_quality_overview()
plus db.finished_goods_fifo_guard_status() -- and returns a JSON-serializable
dict for the Next.js frontend. Nothing here touches Streamlit; pages/control.py
stays live and unchanged. See
memory/project_marketshelper_frontend_migration.md.

Unlike Обзор / Товары / Реклама this screen is NOT period-scoped: pages/control.py
reads only ctx['settings'], ctx['token_exists'], ctx['sync_info'] and
ctx['backup_info'] -- it reflects data quality at the current moment, not over a
date range -- so there is no `period`/`start`/`end` here.

Besides the read-only payload this module also owns the two CSV exports the
Streamlit page offers as st.download_button (the same DataFrames, the same
to_csv(index=False) content, the same utf-8-sig encoding so Excel opens the
Cyrillic headers correctly) and run_fifo_recheck(), the "Полная перепроверка
FIFO" button -- sync_all() followed by process_sales_fifo_events(), returning
the same summary sentence the Streamlit page puts in st.session_state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from db import finished_goods_fifo_guard_status, process_sales_fifo_events
from sync import sync_all
from today_service import _records, json_safe
from ui_helpers import build_data_quality_overview, num

# Sort orders pages/control.py applies to each table before rendering it.
_RELIABILITY_RANK = {"Надёжно": 0, "С ограничениями": 1, "Ненадёжно": 2, "Не настроено": 3, "Отложено": 4}
_STATUS_ORDER = {"Критично": 0, "Внимание": 1, "Ожидание API": 2, "Готово": 3, "Отложено": 4}
_PRIORITY_BY_STATUS = {"Критично": "1 — сейчас", "Внимание": "2 — затем"}

# Page header + every fixed caption/hint pages/control.py prints, verbatim.
TITLE = "Контроль качества данных"
SUBTITLE = (
    "Показывает, каким расчётам можно доверять сейчас и какие данные "
    "действительно блокируют решения."
)
OPTIONAL_NOTE = (
    "Поставщики, закупочные цены и MOQ являются необязательными. Пока вы не "
    "используете реальный сводный план закупок, их отсутствие не снижает "
    "готовность финансов, производства, остатков и FIFO."
)
RELIABILITY_CAPTION = (
    "Статус относится к текущим данным на этом компьютере. Он не является "
    "гарантией результата и не заменяет сверку с кабинетом WB."
)
COST_DIAGNOSTICS_TITLE = "Диагностика сопоставления себестоимости активных товаров"
COST_DIAGNOSTICS_EMPTY = "Активные товары для проверки не определены."
COST_DIAGNOSTICS_OK = "Все активные товары сопоставлены с положительной себестоимостью."
TODO_EMPTY = "Обязательных действий по качеству данных сейчас нет."
COST_ISSUES_TITLE = "Конкретные позиции без подтверждённой ставки"
COST_ISSUES_NOTE = (
    "Товары, для которых себестоимость не сопоставлена автоматически — "
    "стоит проверить вручную."
)
COST_ISSUES_CAPTION = (
    "«Нет строки» и «Нулевая ставка» разделены. Сопоставление сначала "
    "выполняется по артикулу WB, затем — по нормализованному артикулу продавца."
)
OPTIONAL_TITLE = "Необязательные настройки"
OPTIONAL_SUBTITLE = (
    "Проверки, которые не блокируют расчёты, но стоит заполнить для полноты "
    "картины (поставщики, закупочные цены, MOQ)."
)

# Cyrillic DataFrame columns -> API keys. The DataFrames themselves keep their
# original Cyrillic names -- those are what pages/control.py's st.dataframe and
# both CSV exports still use.
_CHECK_COLUMNS = {
    "Модуль": "module", "Проверка": "check", "Статус": "status",
    "Детали": "detail", "Что сделать": "action",
}
_ISSUE_COLUMNS = {"Приоритет": "priority", **_CHECK_COLUMNS}
_RELIABILITY_COLUMNS = {
    "Расчётный контур": "module", "Надёжность": "reliability", "Основание": "basis",
}
_COST_COLUMNS = {
    "Артикул WB": "nm_id", "Артикул продавца": "article", "Товар": "product",
    "Источник активности": "source", "Ставка, ₽": "rate",
    "Сопоставление": "match", "Статус": "status",
}


def _overview(ctx: dict) -> tuple[pd.DataFrame, dict[str, object], pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """The exact four values pages/control.py gets out of
    build_data_quality_overview(), plus the FIFO guard state it reads next --
    both keyed off the same sync_info timestamp the Streamlit page passes."""
    sync_info = ctx.get("sync_info") or {}
    quality_df, quality_summary, reliability_df, cost_quality_df = build_data_quality_overview(
        ctx["settings"], ctx["token_exists"], ctx.get("sync_info"), ctx.get("backup_info"),
    )
    fifo_guard = finished_goods_fifo_guard_status(
        str(sync_info.get("finished_at") or sync_info.get("started_at") or "")
    )
    return quality_df, quality_summary, reliability_df, cost_quality_df, fifo_guard


def _all_checks(quality_df: pd.DataFrame) -> pd.DataFrame:
    """The "Все проверки" table: every check, worst status first, with the
    scoring columns dropped -- and the frame the quality CSV export writes."""
    all_checks = quality_df.copy()
    all_checks["_rank"] = all_checks["Статус"].map(_STATUS_ORDER).fillna(9)
    return all_checks.sort_values(["_rank", "Модуль", "Проверка"]).drop(
        columns=["_rank", "Вес", "Баллы", "Обязательная"]
    )


def _banner(summary: dict[str, object]) -> dict[str, str]:
    """The st.success / st.warning line under the KPI row."""
    if int(summary["critical"]) == 0:
        if int(summary.get("waiting", 0)) > 0:
            return {
                "tone": "good",
                "message": (
                    "Критических проблем нет. Небольшое расхождение FIFO ожидает "
                    "выравнивания API WB и не снижает готовность расчётов."
                ),
            }
        return {
            "tone": "good",
            "message": (
                "Критических проблем нет. Основные расчёты можно использовать; "
                "предупреждения указаны ниже."
            ),
        }
    return {
        "tone": "warn",
        "message": (
            "Есть устойчивые или крупные критические пробелы. Перед действиями "
            "по затронутым модулям выполните рекомендации из таблицы."
        ),
    }


def _guard_card(fifo_guard: dict[str, Any]) -> dict[str, str]:
    """The status box next to the "Полная перепроверка FIFO" button -- the same
    four branches (st.info / st.warning / st.warning / st.success)."""
    status = str(fifo_guard.get("status", "Готово") or "Готово")
    if status == "Ожидание API":
        return {
            "tone": "info",
            "status": status,
            "message": (
                f"FIFO: расхождение {int(fifo_guard.get('total_units', 0) or 0)} ед., "
                f"цикл {int(fifo_guard.get('cycles', 0) or 0)}, "
                f"возраст {float(fifo_guard.get('age_minutes', 0) or 0):.0f} мин. "
                "Ручная сверка временно не требуется."
            ),
        }
    if status == "Внимание" and str(fifo_guard.get("guard_mode", "") or "") == "wb_incident_review":
        return {
            "tone": "warn",
            "status": status,
            "message": str(fifo_guard.get(
                "reason",
                "WB-расхождение требует отдельной проверки и не должно списываться обычной сверкой.",
            )),
        }
    if status == "Критично":
        return {
            "tone": "warn",
            "status": status,
            "message": str(fifo_guard.get("reason", "Устойчивое локальное расхождение FIFO.")),
        }
    return {
        "tone": "good",
        "status": status,
        "message": "FIFO готовой продукции совпадает с физическими остатками.",
    }


def compute_control(ctx: dict) -> dict:
    quality_df, summary, reliability_df, cost_quality_df, fifo_guard = _overview(ctx)

    # The same six kpi_card() calls, in the same order, with the same
    # pre-formatted value/note strings, so the frontend never re-implements the
    # number formatting.
    kpis = [
        {
            "key": "score", "label": "Готовность данных",
            "value": f"{float(summary['score']):.0f}%", "note": "Обязательные проверки",
        },
        {
            "key": "critical", "label": "Критично",
            "value": num(int(summary["critical"])), "note": "Блокирует часть расчётов",
        },
        {
            "key": "waiting", "label": "Ожидание API",
            "value": num(int(summary.get("waiting", 0))), "note": "Не снижает готовность",
        },
        {
            "key": "warnings", "label": "Внимание",
            "value": num(int(summary["warnings"])), "note": "Можно работать с оговорками",
        },
        {
            "key": "ready", "label": "Готово",
            "value": num(int(summary["ready"])), "note": "Включая ожидание API",
        },
        {
            "key": "optional", "label": "Отложено",
            "value": num(int(summary["optional"])), "note": "Не входит в общий балл",
        },
    ]

    reliability_view = reliability_df.copy()
    reliability_view["_rank"] = reliability_view["Надёжность"].map(_RELIABILITY_RANK).fillna(9)
    reliability_view = reliability_view.sort_values("_rank").drop(columns="_rank")

    all_checks = _all_checks(quality_df)

    cost_issues = (
        cost_quality_df[cost_quality_df["Статус"].ne("Покрыт")].copy()
        if not cost_quality_df.empty else pd.DataFrame()
    )
    cost_issue_count = int(len(cost_issues))

    issues = quality_df[quality_df["Статус"].isin(["Критично", "Внимание"])].copy()
    if not issues.empty:
        issues["Приоритет"] = issues["Статус"].map(_PRIORITY_BY_STATUS)
        issues = issues[["Приоритет", "Модуль", "Проверка", "Детали", "Что сделать"]].sort_values("Приоритет")

    optional_rows = quality_df[quality_df["Статус"].eq("Отложено")][
        ["Модуль", "Проверка", "Детали", "Что сделать"]
    ]

    return {
        "title": TITLE,
        "subtitle": SUBTITLE,
        "kpis": json_safe(kpis),
        "summary": json_safe(summary),
        "banner": _banner(summary),
        "optional_note": OPTIONAL_NOTE,
        "fifo_guard": _guard_card(fifo_guard),
        "reliability": {
            "rows": _records(reliability_view, _RELIABILITY_COLUMNS),
            "caption": RELIABILITY_CAPTION,
        },
        "all_checks": {
            "rows": _records(all_checks, _CHECK_COLUMNS),
            "cost_quality": {
                "title": COST_DIAGNOSTICS_TITLE,
                "empty_note": COST_DIAGNOSTICS_EMPTY,
                "ok_note": COST_DIAGNOSTICS_OK,
                "issue_count": cost_issue_count,
                "rows": _records(cost_quality_df, _COST_COLUMNS),
            },
        },
        "todo": {
            "empty_note": TODO_EMPTY,
            "issues": _records(issues, _ISSUE_COLUMNS),
            "cost_issues": {
                "title": COST_ISSUES_TITLE,
                "note": COST_ISSUES_NOTE,
                "caption": COST_ISSUES_CAPTION,
                "rows": _records(
                    cost_issues[["Артикул WB", "Артикул продавца", "Товар", "Ставка, ₽", "Сопоставление", "Статус"]]
                    if not cost_issues.empty else cost_issues,
                    _COST_COLUMNS,
                ),
            },
            "optional": {
                "title": OPTIONAL_TITLE,
                "subtitle": OPTIONAL_SUBTITLE,
                "rows": _records(optional_rows, _CHECK_COLUMNS),
            },
        },
    }


def _csv_response(frame: pd.DataFrame, prefix: str) -> tuple[bytes, str]:
    """(bytes, filename) for a download. utf-8-sig, exactly like the Streamlit
    st.download_button -- without the BOM Excel misreads the Cyrillic headers.
    Same `wb_<name>_<YYYYmmdd_HHMM>.csv` MSK-stamped filename too."""
    stamp = f"{datetime.now(ZoneInfo('Europe/Moscow')):%Y%m%d_%H%M}"
    return frame.to_csv(index=False).encode("utf-8-sig"), f"{prefix}_{stamp}.csv"


def quality_csv(ctx: dict) -> tuple[bytes, str]:
    """"Скачать отчёт качества данных CSV" -- the "Все проверки" table as shown."""
    quality_df, _, _, _, _ = _overview(ctx)
    return _csv_response(_all_checks(quality_df), "wb_data_quality")


def cost_quality_csv(ctx: dict) -> tuple[bytes, str]:
    """"Скачать диагностику себестоимости CSV" -- the whole cost-coverage
    diagnostics frame (all rows, not just the problem ones), as Streamlit does."""
    _, _, _, cost_quality_df, _ = _overview(ctx)
    return _csv_response(cost_quality_df, "wb_cost_quality")


def run_fifo_recheck() -> str:
    """"Полная перепроверка FIFO": a full WB sync followed by the local sales
    FIFO recompute -- the same pair the background sync worker runs on a timer,
    in the same order pages/control.py's button runs them. sync_all() only pulls
    from WB, it never writes to the cabinet. Returns the same summary sentence
    the Streamlit page shows afterwards."""
    sync_result = sync_all()
    fifo_result = process_sales_fifo_events()
    return (
        f"Полная перепроверка завершена. Синхронизация: {sync_result}; "
        f"обработано FIFO-операций: {int(fifo_result.get('processed', 0) or 0)}, "
        f"ошибок: {int(fifo_result.get('errors', 0) or 0)}."
    )
