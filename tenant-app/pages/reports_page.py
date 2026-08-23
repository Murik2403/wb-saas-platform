from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from config import DB_PATH
from report_scheduler import run_definition
from reports.metrics import METRIC_LABELS
from reports.store import ReportStore
from ui_helpers import render_empty_state

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def _generate_now(store: ReportStore, definition: dict) -> None:
    try:
        run_definition(store, definition)
        st.session_state["reports_page_message"] = ("success", f"Отчёт «{definition['name']}» сгенерирован")
    except Exception as exc:
        st.session_state["reports_page_message"] = ("error", f"Не удалось сгенерировать: {exc}")


def render(ctx: dict) -> None:
    st.markdown('<div class="wb-title">Отчёты</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wb-subtitle">PDF-отчёты с графиками по расписанию — за последние 30 дней на момент генерации</div>',
        unsafe_allow_html=True,
    )

    message = st.session_state.pop("reports_page_message", None)
    if message:
        level, text = message
        (st.success if level == "success" else st.error)(text)

    store = ReportStore(DB_PATH)

    with st.expander("Новый отчёт", expanded=False):
        name = st.text_input("Название отчёта", placeholder="Еженедельная сводка")
        metric_choices = st.multiselect(
            "Что включить", options=list(METRIC_LABELS.keys()),
            format_func=lambda code: METRIC_LABELS[code],
            default=list(METRIC_LABELS.keys()),
        )
        schedule_type = st.selectbox("Периодичность", ["daily", "weekly", "monthly"], format_func={
            "daily": "Ежедневно", "weekly": "Еженедельно", "monthly": "Ежемесячно",
        }.get)
        cols = st.columns(2)
        with cols[0]:
            schedule_time = st.time_input("Время (МСК)", value=None)
        schedule_weekday = None
        schedule_day = None
        with cols[1]:
            if schedule_type == "weekly":
                schedule_weekday = WEEKDAYS.index(st.selectbox("День недели", WEEKDAYS))
            elif schedule_type == "monthly":
                schedule_day = st.number_input("Число месяца", min_value=1, max_value=28, value=1)

        if st.button("Сохранить расписание", type="primary"):
            if not name.strip():
                st.error("Укажите название отчёта")
            elif not metric_choices:
                st.error("Выберите хотя бы одну метрику")
            elif schedule_time is None:
                st.error("Укажите время")
            else:
                store.add_definition(
                    name=name.strip(), metrics=metric_choices, schedule_type=schedule_type,
                    schedule_time=schedule_time.strftime("%H:%M"),
                    schedule_weekday=schedule_weekday, schedule_day=schedule_day,
                )
                st.session_state["reports_page_message"] = ("success", "Расписание сохранено")
                st.rerun()

    definitions = store.list_definitions()
    st.markdown(f"### Расписания ({len(definitions)})")
    if not definitions:
        render_empty_state("Пока нет ни одного отчёта", "Создайте расписание выше — первая генерация произойдёт автоматически, либо нажмите «Сгенерировать сейчас».")
    else:
        for definition in definitions:
            metric_names = ", ".join(METRIC_LABELS.get(c, c) for c in json.loads(definition["metrics"]))
            schedule_label = {
                "daily": f"ежедневно в {definition['schedule_time']}",
                "weekly": f"еженедельно по {WEEKDAYS[definition['schedule_weekday']].lower()}ам в {definition['schedule_time']}",
                "monthly": f"ежемесячно {definition['schedule_day']}-го числа в {definition['schedule_time']}",
            }[definition["schedule_type"]]
            with st.container(border=True):
                cols = st.columns([3, 1, 1])
                with cols[0]:
                    st.markdown(f"**{definition['name']}** — {schedule_label}")
                    st.caption(f"Метрики: {metric_names}. Последний запуск: {definition['last_run_at'] or 'ещё не было'}")
                with cols[1]:
                    if st.button("Сгенерировать сейчас", key=f"report_gen_{definition['id']}", use_container_width=True):
                        _generate_now(store, definition)
                        st.rerun()
                with cols[2]:
                    if st.button("Удалить", key=f"report_del_{definition['id']}", use_container_width=True):
                        store.delete_definition(definition["id"])
                        st.session_state["reports_page_message"] = ("success", "Расписание удалено")
                        st.rerun()

    st.markdown("### Готовые файлы")
    runs = [r for r in store.list_runs() if r["status"] == "ok" and r["file_path"]]
    if not runs:
        render_empty_state("Пока нет готовых отчётов", "Появятся здесь после первой генерации.")
    else:
        definitions_by_id = {d["id"]: d["name"] for d in definitions}
        for run in runs[:20]:
            path = Path(run["file_path"])
            if not path.exists():
                continue
            report_name = definitions_by_id.get(run["definition_id"], f"Отчёт #{run['definition_id']}")
            cols = st.columns([3, 1])
            with cols[0]:
                st.caption(f"{report_name} — {run['started_at'][:19]}")
            with cols[1]:
                st.download_button(
                    "Скачать PDF", data=path.read_bytes(), file_name=path.name,
                    mime="application/pdf", key=f"report_dl_{run['id']}", use_container_width=True,
                )
