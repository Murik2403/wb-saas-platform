from __future__ import annotations

import pytest

from store_expense_import import ExpenseCsvError, parse_store_expense_csv


def test_parse_utf8_bom_semicolon_csv_and_russian_decimal_comma():
    rows = parse_store_expense_csv(
        "\ufeffarticle;amount_rub;period_start;period_end;note\n"
        "storage;1250,50;2026-09-01;2026-09-30;Аренда склада\n".encode("utf-8")
    )
    assert rows == [{
        "line_number": 2,
        "article": "storage",
        "amount_rub": 1250.5,
        "period_start": "2026-09-01",
        "period_end": "2026-09-30",
        "note": "Аренда склада",
        "errors": [],
        "duplicate": False,
    }]


def test_parser_keeps_invalid_rows_for_preview_and_flags_within_file_duplicates():
    rows = parse_store_expense_csv(
        b"article,amount_rub,period_start,period_end,note\n"
        b"taxes,300,2026-09-01,2026-09-30,USN\n"
        b"taxes,300,2026-09-01,2026-09-30,USN\n"
        b"unknown,NaN,2026-09-30,2026-09-01,bad\n"
    )
    assert rows[0]["errors"] == []
    assert rows[0]["duplicate"] is False
    assert rows[1]["duplicate"] is True
    assert rows[2]["duplicate"] is False
    assert len(rows[2]["errors"]) == 3


@pytest.mark.parametrize("content", [
    b"article,amount_rub,period_start\ntaxes,1,2026-09-01\n",
    b"article,amount_rub,period_start,period_end,unsupported\ntaxes,1,2026-09-01,2026-09-02,x\n",
    b"article,amount_rub,period_start,period_end\nwb_commission,1,2026-09-01,2026-09-02\n",
    b"article,amount_rub,period_start,period_end\ntaxes,1,01.09.2026,2026-09-02\n",
    b"article,amount_rub,period_start,period_end\ntaxes,1,2026-09-03,2026-09-02\n",
])
def test_csv_validation_returns_errors_or_rejects_unsupported_structure(content):
    if content.startswith(b"article,amount_rub,period_start\n") or b"unsupported" in content:
        with pytest.raises(ExpenseCsvError):
            parse_store_expense_csv(content)
        return
    row = parse_store_expense_csv(content)[0]
    assert row["errors"]


def test_csv_rejects_invalid_encoding_file_size_and_row_limit():
    with pytest.raises(ExpenseCsvError, match="UTF-8"):
        parse_store_expense_csv(b"\xff")
    with pytest.raises(ExpenseCsvError, match="5 МиБ"):
        parse_store_expense_csv(b"x" * (5 * 1024 * 1024 + 1))
    rows = "article,amount_rub,period_start,period_end\n" + "taxes,1,2026-09-01,2026-09-01\n" * 501
    with pytest.raises(ExpenseCsvError, match="500"):
        parse_store_expense_csv(rows.encode())


@pytest.mark.parametrize("row", [
    b"taxes,100,2026-09-01,2026-09-30,note,unexpected\n",
    b"taxes,100,2026-09-01\n",
])
def test_csv_rejects_rows_with_extra_or_missing_cells(row):
    content = b"article,amount_rub,period_start,period_end,note\n" + row
    with pytest.raises(ExpenseCsvError, match="число ячеек"):
        parse_store_expense_csv(content)
