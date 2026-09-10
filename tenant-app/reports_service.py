"""Pure computation for the "Отчёты" (Reports) screen.

Same role as today_service.py / overview_service.py / ads_service.py: it wraps
exactly what pages/reports_page.py renders and returns a JSON-serializable dict
for the Next.js frontend. Nothing here touches Streamlit; pages/reports_page.py
stays live and unchanged. See
memory/project_marketshelper_frontend_migration.md.

Unlike the other migrated screens this one is not period-scoped and does not
read build_dashboard at all -- reports carry their own implicit window (see
report_scheduler._SCHEDULE_PERIOD_DAYS), so there is no `ctx` here, just the
tenant's ReportStore. It is also the first screen with mutations; the write
paths (create/delete a schedule, generate on demand, request a Telegram link
code) live in api_server.py's routes, this module holds the read model, the
copy and the validation that pages/reports_page.py does inline.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from reports import delivery
from reports.metrics import METRIC_LABELS
from reports.store import ReportStore
from today_service import json_safe

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

SCHEDULE_TYPES = ("daily", "weekly", "monthly")

# pages/reports_page.py's st.selectbox format_func, as data.
SCHEDULE_TYPE_LABELS = {"daily": "Ежедневно", "weekly": "Еженедельно", "monthly": "Ежемесячно"}

# The page title/subtitle and every render_section_header/render_empty_state
# call in pages/reports_page.py, kept verbatim (same idea as ads_service's
# EMPTY_STATE) so the wording lives next to the port, not in the TSX.
COPY = {
    "title": "Отчёты",
    "subtitle": (
        "PDF-отчёты с графиками по расписанию — период подбирается автоматически: "
        "1 день для ежедневных, 7 для еженедельных, 30 для ежемесячных"
    ),
    "telegram_header": "Telegram",
    "telegram_hint": (
        "Привяжите Telegram-чат, чтобы получать отчёты сразу в мессенджер, в дополнение к email."
    ),
    "telegram_linked": "Telegram-чат привязан к аккаунту.",
    "telegram_caption": "Привяжите Telegram, чтобы получать отчёты сразу в чат с ботом поддержки.",
    "schedules_hint": (
        "Настроенные автоматические отчёты: периодичность, метрики и способ доставки. "
        "«Удалить» + создать заново — единственный способ изменить состав метрик расписания."
    ),
    "schedules_empty_title": "Пока нет ни одного отчёта",
    "schedules_empty_note": (
        "Создайте расписание выше — первая генерация произойдёт автоматически, "
        "либо нажмите «Сгенерировать сейчас»."
    ),
    "files_header": "Готовые файлы",
    "files_hint": "Последние 20 сгенерированных отчётов, доступные для скачивания в формате PDF.",
    "files_empty_title": "Пока нет готовых отчётов",
    "files_empty_note": "Появятся здесь после первой генерации.",
    "email_unavailable": "Отправка на email недоступна: не настроен внутренний API шлюза.",
    "telegram_unavailable": "Отправка в Telegram недоступна: сначала привяжите чат в разделе выше.",
}

# Streamlit's runs list is `store.list_runs()[:20]` -- the same 20 here.
RUNS_LIMIT = 20


def schedule_label(definition: dict) -> str:
    """Port of pages/reports_page.py's _schedule_label -- same branch-by-branch
    build (not a dict literal) so WEEKDAYS[None] never gets evaluated for a
    daily/monthly definition whose schedule_weekday is NULL."""
    schedule_type = definition["schedule_type"]
    if schedule_type == "weekly":
        return f"еженедельно по {WEEKDAYS[definition['schedule_weekday']].lower()}ам в {definition['schedule_time']}"
    if schedule_type == "monthly":
        return f"ежемесячно {definition['schedule_day']}-го числа в {definition['schedule_time']}"
    return f"ежедневно в {definition['schedule_time']}"


# The @st.cache_data(ttl=30) around delivery.telegram_is_linked() in
# pages/reports_page.py, as a plain in-process cache: a network round-trip to
# the gateway on every page load would be wasteful and slow, and 30s is short
# enough that a just-completed /link shows up quickly. `refresh=True` is the
# "Обновить статус" button's .clear().
_TELEGRAM_CACHE_TTL_SECONDS = 30
_telegram_cache: tuple[float, bool] | None = None


def telegram_is_linked_cached(*, refresh: bool = False) -> bool:
    global _telegram_cache
    now = time.monotonic()
    if not refresh and _telegram_cache is not None and now - _telegram_cache[0] < _TELEGRAM_CACHE_TTL_SECONDS:
        return _telegram_cache[1]
    linked = delivery.telegram_is_linked()
    _telegram_cache = (now, linked)
    return linked


def _definition_view(definition: dict) -> dict:
    """One schedule card, with the same three strings pages/reports_page.py
    composes inline: the metric list, the delivery note and the last-run text."""
    metric_codes = json.loads(definition["metrics"])
    delivery_notes = []
    if definition.get("email_enabled"):
        delivery_notes.append("на email")
    if definition.get("telegram_enabled"):
        delivery_notes.append("в Telegram")
    return {
        "id": definition["id"],
        "name": definition["name"],
        "metrics": metric_codes,
        "metric_names": ", ".join(METRIC_LABELS.get(c, c) for c in metric_codes),
        "schedule_type": definition["schedule_type"],
        "schedule_label": schedule_label(definition),
        "email_enabled": bool(definition.get("email_enabled")),
        "telegram_enabled": bool(definition.get("telegram_enabled")),
        "delivery_note": f", также {' и '.join(delivery_notes)}" if delivery_notes else "",
        "last_run_at": definition["last_run_at"],
        "last_run_label": definition["last_run_at"] or "ещё не было",
    }


def list_downloadable_runs(store: ReportStore, definitions: list[dict]) -> list[dict]:
    """The "Готовые файлы" list: successful runs whose PDF is still on disk,
    newest first, capped at RUNS_LIMIT -- exactly pages/reports_page.py's
    filter chain (status == "ok", non-empty file_path, path.exists()).

    Also the allow-list the download route validates against: a run_id that
    isn't in here isn't servable."""
    names_by_id = {d["id"]: d["name"] for d in definitions}
    rows: list[dict] = []
    for run in store.list_runs():
        if run["status"] != "ok" or not run["file_path"]:
            continue
        path = Path(run["file_path"])
        if not path.exists():
            continue
        rows.append({
            "id": run["id"],
            "definition_id": run["definition_id"],
            "report_name": names_by_id.get(run["definition_id"], f"Отчёт #{run['definition_id']}"),
            "started_at": run["started_at"],
            "started_label": run["started_at"][:19],
            "file_name": path.name,
            "file_path": str(path),
        })
        if len(rows) >= RUNS_LIMIT:
            break
    return rows


def compute_reports(store: ReportStore, *, refresh_telegram: bool = False) -> dict:
    telegram_configured = delivery.internal_api_is_configured()
    # Streamlit only calls telegram_is_linked() from inside the Telegram
    # section, which it only renders when the internal API is configured --
    # skip the (guaranteed-False) round-trip the same way.
    telegram_linked = telegram_is_linked_cached(refresh=refresh_telegram) if telegram_configured else False

    definitions = store.list_definitions()
    runs = list_downloadable_runs(store, definitions)

    return json_safe({
        "copy": COPY,
        "telegram_configured": telegram_configured,
        "telegram_linked": telegram_linked,
        "email_configured": delivery.email_is_configured(),
        "metric_options": [{"code": code, "label": label} for code, label in METRIC_LABELS.items()],
        "schedule_type_options": [
            {"code": code, "label": SCHEDULE_TYPE_LABELS[code]} for code in SCHEDULE_TYPES
        ],
        "weekdays": WEEKDAYS,
        "schedules": [_definition_view(d) for d in definitions],
        # file_path is server-side only -- the frontend downloads by run id.
        "runs": [{k: v for k, v in run.items() if k != "file_path"} for run in runs],
    })


def validate_new_schedule(
    *, name: str, metrics: list[str], schedule_type: str,
    schedule_time: str | None, schedule_weekday: int | None, schedule_day: int | None,
) -> str | None:
    """Returns the Russian error message pages/reports_page.py's st.error would
    show, or None if the form is valid. The first three checks are that page's
    own, in the same order; the rest re-state constraints Streamlit's widgets
    enforce structurally (a selectbox/number_input can't produce an out-of-range
    value, an HTTP body can)."""
    if not name.strip():
        return "Укажите название отчёта"
    if not metrics:
        return "Выберите хотя бы одну метрику"
    if not schedule_time:
        return "Укажите время"
    unknown = [c for c in metrics if c not in METRIC_LABELS]
    if unknown:
        return f"Неизвестная метрика: {', '.join(unknown)}"
    if schedule_type not in SCHEDULE_TYPES:
        return "Неизвестная периодичность"
    if schedule_type == "weekly" and schedule_weekday not in range(7):
        return "Укажите день недели"
    if schedule_type == "monthly" and (schedule_day is None or not 1 <= schedule_day <= 28):
        return "Укажите число месяца от 1 до 28"
    return None
