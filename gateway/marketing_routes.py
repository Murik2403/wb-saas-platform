"""Public, server-rendered learning pages; no customer data or writes."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import config


MARKETING_PAGES = (
    ("/calculator", "marketing_calculator_page.html", "Калькулятор предварительной маржи WB", "Предварительная маржа по введённым затратам: цена, себестоимость, комиссия, логистика и реклама. Расчёт в браузере без отправки значений."),
    ("/dlya-proizvoditeley-wb", "marketing_producers.html", "Учёт производства для продавцов Wildberries", "Сырьё, выпуск партии и себестоимость: рабочий сценарий для собственного и контрактного производства на Wildberries."),
    ("/sebestoimost-wb", "marketing_cost.html", "Себестоимость товара на Wildberries", "Как связать материалы, выпуск, упаковку и расходы Wildberries. Состав расчёта и пример на одной партии."),
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
    ("/vs-1c", "marketing_vs_1c.html", "MARKETSHELPER или 1С: что выбрать производителю на WB", "Сравнение с 1С:УНФ, УТ, КА и Бухгалтерией для производителя на Wildberries: производство, партии, интеграция с WB. Где 1С подходит лучше."),
)
PUBLIC_MARKETING_PATHS = tuple(page[0] for page in MARKETING_PAGES)


def create_marketing_router(templates: Jinja2Templates) -> APIRouter:
    """Reuse the gateway's configured template environment and privacy policy."""
    router = APIRouter()

    def endpoint_for(template_name: str, title: str, description: str):
        async def page(request: Request):
            return templates.TemplateResponse(request, template_name, {
                "page_title": title,
                "page_description": description,
                "trial_days": config.TRIAL_DAYS,
                "price_rub": config.SUBSCRIPTION_PRICE_RUB,
                "period_days": config.BILLING_PERIOD_DAYS,
                "demo_url": config.tenant_url("demo"),
            })
        return page

    for path, template_name, title, description in MARKETING_PAGES:
        router.add_api_route(path, endpoint_for(template_name, title, description),
                             methods=["GET"], response_class=HTMLResponse,
                             name="marketing_" + template_name.removesuffix(".html"))
    return router
