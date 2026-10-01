from datetime import date
from io import BytesIO

import pandas as pd
from openpyxl import load_workbook

from report_tools import build_finance_excel


def test_export_keeps_external_text_literal_and_signed_money_and_unknown_cogs():
    # Names and raw WB fields are external text, even if they look like Excel
    # expressions. Include headers as well as cells and every optional sheet.
    frame = pd.DataFrame({"=header": ["=1+1"], "Сумма": [-12.34]})
    original = frame.copy(deep=True)
    contents = build_finance_excel(
        start=date(2026, 9, 1), end=date(2026, 9, 30),
        financial={"cost": -40, "exact_fifo_cogs_rub": -40,
                   "insufficient_cogs_units": 2, "cogs_complete": False},
        ad_spend=0, financial_products=frame, expense_rows=frame,
        reconciliation=frame, wb_detail=frame, store_operations=frame,
    )
    workbook = load_workbook(BytesIO(contents), data_only=False)
    for name in ("Юнит-экономика", "Статьи расходов", "Сверка WB",
                 "Выгрузка WB", "Операции магазина"):
        sheet = workbook[name]
        assert sheet["A1"].value == "=header"
        assert sheet["A1"].data_type == "s"
        assert sheet["A2"].value == "=1+1"
        assert sheet["A2"].data_type == "s"
        assert sheet["B2"].value == -12.34
        assert sheet["B2"].data_type == "n"
    summary = {r[0].value: r[1].value for r in workbook["Сводка"].iter_rows(min_row=4)}
    assert summary["Из неё: точный исторический FIFO, ₽"] == -40
    assert summary["Единиц без подтверждённой себестоимости, шт"] == 2
    assert summary["Покрытие себестоимости полное"] is False
    pd.testing.assert_frame_equal(frame, original)
