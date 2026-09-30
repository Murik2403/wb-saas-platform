"""Public, server-rendered learning pages; no customer data or writes."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import config
from training_materials import create_training_media_router, training_context


MARKETING_PAGES = (
    ("/training", "marketing_training.html", "Обучение работе в кабинете", "Учебные видео и инструкции по всем разделам MARKETSHELPER: товары, склад, финансы, производство и настройки. Примеры на вымышленных данных."),
    ("/updates", "marketing_updates.html", "Обновления сервиса — MARKETSHELPER", "Выпущенные изменения MARKETSHELPER: учёт партий, перенос данных, движение денег и проверка источников себестоимости."),
    ("/calculator", "marketing_calculator_page.html", "Калькулятор маржи WB по девяти статьям расходов — MARKETSHELPER", "Рассчитайте результат на единицу по закупке или производству, комиссии, логистике, хранению, рекламе, упаковке, браку и налогам. Браузерный калькулятор, CSV и ссылка без регистрации."),
    ("/dlya-proizvoditeley-wb", "marketing_producers.html", "Учёт производства для продавцов Wildberries", "Сырьё, выпуск партии и себестоимость: рабочий сценарий для собственного и контрактного производства на Wildberries."),
    ("/sebestoimost-wb", "marketing_cost.html", "Себестоимость товара на WB: расчёт и учёт партий", "Как рассчитать себестоимость товара на Wildberries: материалы, выпуск и партии FIFO. Пример затрат, проверка расходов WB и калькулятор предварительной маржи."),
    ("/fifo-uchet", "marketing_fifo.html", "FIFO-учёт сырья и товаров по партиям", "Понятный пример FIFO: списание партий в порядке поступления, стоимость выпуска и оценка оставшихся запасов."),
    ("/guides", "marketing_guides.html", "Практические руководства для производителей на WB", "FIFO, цена акции, сверка остатков, НЗП и переход из Excel: понятные расчёты и проверочные списки для учёта своего производства на WB."),
    ("/guides/fifo-po-partiyam", "marketing_guide_fifo.html", "Как считать FIFO по партиям: пример и проверка", "Разбираем списание двух партий сырья, себестоимость выпуска и контроль остатков. Учебный пример с проверяемой арифметикой."),
    ("/guides/pribyl-i-rashody-wb", "marketing_guide_profit.html", "Прибыль на WB: какие расходы проверить", "Как отделить себестоимость, расходы продажи и движение денег. Пример расчёта результата и проверка исходных данных."),
    ("/guides/iz-excel-v-uchet", "marketing_guide_excel.html", "Как перейти из Excel к учёту партий", "План перехода на одном товаре: подготовка справочников, начальных остатков, партий и контрольного расчёта."),
    ("/guides/fifo-ili-srednyaya-v-akcii", "marketing_guide_fifo_akcii.html", "Цена акции на WB: проверка по стоимости партий на складе", "Как стоимость остатка по FIFO и по средней меняет минимальную цену акции. Условный пример с тремя партиями, проверяемой арифметикой и списком проверки."),
    ("/guides/sverka-ostatkov-wb", "marketing_guide_sverka.html", "Сверка остатков с Wildberries: причины расхождений и шаги", "Что сравнивать с остатками WB, как учесть товар в пути, приёмку и пересорт и пройти сверку по шагам. Условный пример на пяти баркодах и список проверки."),
    ("/guides/nezavershennoe-proizvodstvo", "marketing_guide_nzp.html", "Незавершённое производство: где деньги между сырьём и продажей", "Где лежат деньги производителя между закупкой сырья и выплатой WB, как оценить НЗП и сверить баланс. Сквозной пример на условных числах."),
    ("/sverka-ostatkov-wb", "marketing_sverka.html", "Сервис сверки остатков WB: расхождение по каждому артикулу", "MARKETSHELPER сравнивает ваш учёт с остатками Wildberries по каждому артикулу с учётом товара в пути и показывает расхождение в штуках и рублях."),
    ("/guides/zamena-servisov-analitiki", "marketing_guide_zamena.html", "Как выбрать сервис аналитики WB: рынок, свой магазин или учёт", "Рынок, свой магазин или учёт себестоимости: какой класс инструментов нужен под задачу, что изменилось в данных WB в 2025 году и что проверить перед переходом."),
    ("/ob-avtore", "marketing_author.html", "Об авторе: Мурат Вороков, основатель MARKETSHELPER", "Кто пишет руководства MARKETSHELPER: Мурат Вороков, основатель сервиса и продавец на Wildberries. Задачи, из которых вырос сервис, и темы, в которых он не эксперт."),
)
PUBLIC_MARKETING_PATHS = tuple(page[0] for page in MARKETING_PAGES)

# Authorship of the guides. Only facts that are also visible on the page and
# verifiable from /contacts -- the site is run by a single ИП who sells on WB.
AUTHOR_NAME = "Мурат Вороков"
AUTHOR_ROLE = "основатель MARKETSHELPER, продавец на Wildberries"
AUTHOR_LEGAL_NAME = "Индивидуальный предприниматель Вороков Мурат Валерьевич"
# The page the byline and the Person @id in the JSON-LD point at.
AUTHOR_PATH = "/ob-avtore"

# Publication / last-change dates for the guides, kept here rather than in the
# templates so the visible byline and the Article JSON-LD cannot drift apart.
# Published = the commit that added the template; modified = the last edit.
GUIDE_DATES: dict[str, tuple[str, str]] = {
    "/guides/fifo-po-partiyam": ("2026-09-18", "2026-09-20"),
    "/guides/pribyl-i-rashody-wb": ("2026-09-18", "2026-09-20"),
    "/guides/iz-excel-v-uchet": ("2026-09-18", "2026-09-20"),
    "/guides/fifo-ili-srednyaya-v-akcii": ("2026-09-19", "2026-09-20"),
    "/guides/sverka-ostatkov-wb": ("2026-09-19", "2026-09-19"),
    "/guides/nezavershennoe-proizvodstvo": ("2026-09-19", "2026-09-19"),
    "/guides/zamena-servisov-analitiki": ("2026-09-19", "2026-09-20"),
}

# Guides the author page links to, in site order; derived so a new guide is
# listed on the author page by adding it to MARKETING_PAGES and GUIDE_DATES.
AUTHOR_GUIDES = tuple((path, title) for path, _, title, _ in MARKETING_PAGES if path in GUIDE_DATES)


def llms_txt(base_url: str) -> str:
    """Plain-text site summary for AI crawlers, built from MARKETING_PAGES."""
    lines = [
        "# MARKETSHELPER",
        "",
        "Веб-сервис учёта для продавцов Wildberries: себестоимость товара, партии и",
        "остатки по FIFO, нормы материалов, производственные смены и сверка остатков с WB.",
        "",
        "## Чего сервис не делает",
        "- Это не бухгалтерия и не налоговая отчётность: декларации и проводки остаются за бухгалтером.",
        "- Это не внешняя аналитика ниш и конкурентов: сервис работает с данными вашего кабинета.",
        "- Для WB доступны производственный учёт и общий склад с явными соответствиями магазинов и вариантов.",
        "- По Ozon доступны ограниченная загрузка API и отдельный ручной документальный учёт партий, продаж и возвратов. Автоматическая денежная нормализация API не реализована.",
        "",
        "## Публичные страницы",
    ]
    lines += [f"- {base_url}{path}: {description}" for path, _, _, description in MARKETING_PAGES]
    lines += [
        "",
        "## Автор материалов",
        f"{AUTHOR_NAME} — {AUTHOR_ROLE}. Подробнее: {base_url}{AUTHOR_PATH}",
        "",
    ]
    return "\n".join(lines)


def create_marketing_router(templates: Jinja2Templates) -> APIRouter:
    """Reuse the gateway's configured template environment and privacy policy."""
    router = APIRouter()
    router.include_router(create_training_media_router())

    def endpoint_for(path: str, template_name: str, title: str, description: str):
        published, modified = GUIDE_DATES.get(path, (None, None))

        async def page(request: Request):
            return templates.TemplateResponse(request, template_name, {
                "page_title": title,
                "page_description": description,
                "article_published": published,
                "article_modified": modified,
                "author_name": AUTHOR_NAME,
                "author_role": AUTHOR_ROLE,
                "author_path": AUTHOR_PATH,
                "author_legal_name": AUTHOR_LEGAL_NAME,
                "author_guides": AUTHOR_GUIDES,
                "trial_days": config.TRIAL_DAYS,
                "price_rub": config.SUBSCRIPTION_PRICE_RUB,
                "period_days": config.BILLING_PERIOD_DAYS,
                "demo_url": config.tenant_url("demo"),
                **(training_context() if path == "/training" else {}),
            })
        return page

    for path, template_name, title, description in MARKETING_PAGES:
        router.add_api_route(path, endpoint_for(path, template_name, title, description),
                             methods=["GET"], response_class=HTMLResponse,
                             name="marketing_" + template_name.removesuffix(".html"))
    return router
