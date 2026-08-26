"""Per-page onboarding hints shown above each dashboard section.

One hook point (app.py, right before `page_module.render(ctx)`) covers all
12 nav pages -- no per-page changes needed. See render_setup_checklist in
ui_helpers.py for the sibling first-run ("Первые шаги") onboarding element;
this module is for the ongoing "what does this page do" hint, not the
empty-database welcome flow.
"""
from __future__ import annotations

from html import escape

import streamlit as st

from config import save_settings

# title, body -- one entry per app.py PAGES key. Body is 2-3 short sentences,
# ending with a concrete next action where one makes sense (same tone as
# ui_helpers.render_setup_checklist).
PAGE_HINTS: dict[str, tuple[str, str]] = {
    "Сегодня": (
        "Оперативная сводка на сегодня",
        "Воронка заказов за сегодня, что срочно требует внимания и чего не хватает для "
        "выполнения производственного плана. Загляните сюда в начале дня, чтобы понять, "
        "что горит прямо сейчас.",
    ),
    "Обзор": (
        "Ключевые показатели за период",
        "Прибыль, заказы, выкупы, реклама и остатки за выбранный период — с сравнением к "
        "предыдущему такому же периоду. Начните отсюда, чтобы понять общую картину бизнеса.",
    ),
    "Финансы": (
        "Точная прибыль по данным Wildberries",
        "Финансовый результат за период, сверенный с отчётом о реализации WB, плюс выгрузка "
        "в Excel. Смотрите сюда, когда нужна точная, а не оценочная цифра прибыли.",
    ),
    "Производство": (
        "Учёт производства и склада сырья",
        "Закрытие смен, выдача материалов, упаковка партий, отгрузки и приёмки. Раздел для "
        "тех, кто сам производит товар, а не только перепродаёт готовый.",
    ),
    "Закупки": (
        "Заказы поставщикам",
        "Создание и отслеживание закупочных заказов, приёмка товара и учёт оплат поставщикам.",
    ),
    "Товары": (
        "Экономика по каждому товару",
        "Таблица по SKU: выручка, процент выкупа, расходы на рекламу, ДРР, скорость продаж, "
        "запас в днях и маржа до вычета сборов WB.",
    ),
    "Реклама": (
        "Расходы на рекламу WB",
        "Показатели по рекламным кампаниям: сколько заказов принесла реклама, сколько "
        "потрачено, доля рекламных расходов (ДРР) и средний CTR.",
    ),
    "Остатки": (
        "Склад и себестоимость",
        "Остатки по складам, стоимость запасов методом FIFO и инструменты сверки при "
        "расхождениях с данными Wildberries.",
    ),
    "Контроль": (
        "Статус синхронизации и бэкапов",
        "Когда последний раз обновлялись данные, есть ли резервная копия, и можно вручную "
        "запустить синхронизацию, если что-то выглядит устаревшим.",
    ),
    "Агенты": (
        "ИИ-рекомендации по ценам и рекламе",
        "Автономные агенты предлагают изменения цен и рекламных ставок — ничего не "
        "применяется само, каждую рекомендацию нужно одобрить или отклонить вручную.",
    ),
    "Отчёты": (
        "Автоматические PDF-отчёты",
        "Настройте расписание (ежедневно / еженедельно / ежемесячно) — отчёт с графиками "
        "придёт на email или в привязанный Telegram-чат.",
    ),
    "Настройки": (
        "Подключение Wildberries и себестоимость",
        "Здесь подключается API-токен Wildberries и настраивается себестоимость товаров — "
        "с этого стоит начать работу с кабинетом.",
    ),
}


def render_page_hint(page_name: str, settings: dict) -> None:
    """Dismissible onboarding card in "novice" mode; a small recall popover
    otherwise (or once the card has been dismissed). Dismissal is stored in
    settings["dismissed_hints"] via save_settings(), so -- like ui_mode --
    it persists across reloads and sessions instead of living only in
    st.session_state."""
    hint = PAGE_HINTS.get(page_name)
    if hint is None:
        return
    title, body = hint

    dismissed = settings.get("dismissed_hints") or []
    if settings.get("ui_mode") == "novice" and page_name not in dismissed:
        with st.container(border=True):
            st.markdown(f'<div class="hint-card-title">{escape(title)}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="hint-card-body">{escape(body)}</div>', unsafe_allow_html=True)
            if st.button("Понятно, скрыть", key=f"hint_dismiss_{page_name}"):
                settings["dismissed_hints"] = [*dismissed, page_name]
                save_settings(settings)
                st.rerun()
    else:
        with st.popover("❓ Об этом разделе"):
            st.markdown(f"**{escape(title)}**")
            st.write(body)
