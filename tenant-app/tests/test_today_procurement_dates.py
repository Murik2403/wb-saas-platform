"""Optional purchase dates must not break the Today/Supply dashboard."""
from datetime import date

import pandas as pd
import pytest

from calculations import build_dashboard
import db.core as core
from db.procurement import create_procurement_order
from today_service import compute_today


@pytest.mark.parametrize(
    "expected_date,payment_due_date,status,overdue,payment_due",
    [
        (None, None, "Запланировано", False, False),
        (None, "2026-09-20", "Запланировано", False, True),
        ("2026-09-19", None, "Запланировано", True, False),
        ("2026-09-20", "2026-09-20", "Запланировано", False, True),
        ("2026-09-21", "2026-09-21", "Запланировано", False, False),
        ("2026-09-19", "2026-09-19", "Отменено", False, False),
    ],
)
def test_purchase_dates_keep_orders_visible_and_deadlines_correct(
    monkeypatch, tmp_path, expected_date, payment_due_date, status,
    overdue, payment_due,
):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "today-purchases.sqlite3")
    core.init_db()
    today = date(2026, 9, 20)
    order_id = create_procurement_order(
        procurement_type="Товар", supplier_name="Synthetic supplier",
        status=status, order_date=today, payment_due_date=payment_due_date,
        expected_date=expected_date, note="",
        items=pd.DataFrame([{
            "supplier_article": "QA-UNDATED", "product_name": "Synthetic item",
            "quantity": 3, "supplier_unit_price": 125.5,
        }]),
    )

    result = compute_today({
        "data": build_dashboard(today, today), "settings": {},
        "sync_info": None, "backup_info": None, "today_msk": today,
    })

    orders = result["tabs"]["procurement"]["active_orders"]
    assert [row["id"] for row in orders] == ([] if status == "Отменено" else [order_id])
    if orders:
        assert orders[0]["total_amount"] == pytest.approx(376.5)
    signal_types = [row["type"] for row in result["signals"]]
    assert signal_types.count("Закупка просрочена") == int(overdue)
    assert signal_types.count("Оплата поставщику") == int(payment_due)
