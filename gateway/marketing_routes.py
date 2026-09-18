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
    ("/guides", "marketing_guides.html", "Практические руководства для производителей на WB", "FIFO, расходы Wildberries и переход из Excel: понятные расчёты и проверочные списки для учёта своего производства."),
    ("/guides/fifo-po-partiyam", "marketing_guide_fifo.html", "Как считать FIFO по партиям: пример и проверка", "Разбираем списание двух партий сырья, себестоимость выпуска и контроль остатков. Учебный пример с проверяемой арифметикой."),
    ("/guides/pribyl-i-rashody-wb", "marketing_guide_profit.html", "Прибыль на WB: какие расходы проверить", "Как отделить себестоимость, расходы продажи и движение денег. Пример расчёта результата и проверка исходных данных."),
    ("/guides/iz-excel-v-uchet", "marketing_guide_excel.html", "Как перейти из Excel к учёту партий", "План перехода на одном товаре: подготовка справочников, начальных остатков, партий и контрольного расчёта."),
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
