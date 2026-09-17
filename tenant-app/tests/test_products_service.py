import pandas as pd

from products_service import compute_products


def test_products_api_uses_reconciled_net_margin_and_percentage():
    class Data:
        products = pd.DataFrame([{
            "Артикул WB": 101,
            "Артикул продавца": "A-101",
            "Товар": "Товар",
            "Выручка": 1000.0,
            "Остаток": 4,
            "Запас, дней": 2.0,
            "Себестоимость ед.": 100.0,
            "Маржа до комиссий WB": 700.0,
        }])
        financial_products = pd.DataFrame([{
            "Артикул WB": 101,
            "Чистая прибыль": 120.0,
            "Чистая маржинальность, %": 12.0,
        }])

    payload = compute_products({
        "data": Data(),
        "period": "90d",
        "period_label": "90 дней",
        "start": None,
        "end": None,
    })

    row = payload["products"][0]
    assert row["margin"] == 120.0
    assert row["margin_pct"] == 12.0
    assert row["margin"] != 700.0
