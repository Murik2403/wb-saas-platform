from __future__ import annotations

from datetime import date

from db.manual_expenses import delete_manual_expense, read_manual_expenses, save_manual_expense

from .base import DbTestCase


class ManualExpenseTests(DbTestCase):
    def test_save_read_filter_and_delete(self) -> None:
        expense_id = save_manual_expense(
            nm_id=123, article="taxes", amount_rub=600, allocation_basis="total",
            source="manual", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
            note="УСН",
        )
        rows = read_manual_expenses(nm_id=123, start="2026-09-15", end="2026-10-01")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], expense_id)
        self.assertEqual(rows[0]["allocation_basis"], "total")
        self.assertTrue(delete_manual_expense(expense_id))
        self.assertFalse(delete_manual_expense(expense_id))
        self.assertEqual(read_manual_expenses(nm_id=123), [])

    def test_rejects_invalid_article_and_period(self) -> None:
        with self.assertRaises(ValueError):
            save_manual_expense(
                nm_id=123, article="rent", amount_rub=1, allocation_basis="total",
                source="manual", period_start="2026-09-30", period_end="2026-09-01",
            )
