from __future__ import annotations

from datetime import date

from db.manual_expenses import delete_manual_expense, read_manual_expenses, save_manual_expense, set_manual_expense_inclusion

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
        self.assertEqual(rows[0]["include_in_management_result"], 0)
        self.assertTrue(delete_manual_expense(expense_id))
        self.assertFalse(delete_manual_expense(expense_id))
        self.assertEqual(read_manual_expenses(nm_id=123), [])

    def test_rejects_invalid_article_and_period(self) -> None:
        with self.assertRaises(ValueError):
            save_manual_expense(
                nm_id=123, article="rent", amount_rub=1, allocation_basis="total",
                source="manual", period_start="2026-09-30", period_end="2026-09-01",
            )

    def test_explicit_opt_in_is_persisted_and_commission_cannot_be_opted_in(self) -> None:
        expense_id = save_manual_expense(
            nm_id=123, article="taxes", amount_rub=600, allocation_basis="total",
            source="manual", period_start="2026-09-01", period_end="2026-09-30",
            include_in_management_result=True,
        )
        self.assertEqual(read_manual_expenses(nm_id=123)[0]["include_in_management_result"], 1)
        self.assertFalse(set_manual_expense_inclusion(9999, True))
        self.assertTrue(set_manual_expense_inclusion(expense_id, False))
        self.assertEqual(read_manual_expenses(nm_id=123)[0]["include_in_management_result"], 0)
        with self.assertRaisesRegex(ValueError, "Комиссия WB"):
            save_manual_expense(
                nm_id=123, article="wb_commission", amount_rub=10, allocation_basis="total",
                source="manual", period_start="2026-09-01", period_end="2026-09-30",
                include_in_management_result=True,
            )

    def test_legacy_rows_are_preserved_but_migrate_as_not_included(self) -> None:
        from db.core import connect, init_db

        with connect() as conn:
            conn.execute("DROP TABLE manual_expenses")
            conn.execute("""
                CREATE TABLE manual_expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, nm_id INTEGER NOT NULL,
                    article TEXT NOT NULL, amount_rub REAL NOT NULL,
                    allocation_basis TEXT NOT NULL, source TEXT NOT NULL,
                    period_start TEXT NOT NULL, period_end TEXT NOT NULL,
                    note TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""INSERT INTO manual_expenses(
                id,nm_id,article,amount_rub,allocation_basis,source,period_start,period_end,note,created_at,updated_at
            ) VALUES (7,123,'taxes',100,'total','manual','2026-09-01','2026-09-30','','now','now')""")

        init_db()
        row = read_manual_expenses(nm_id=123)[0]
        self.assertEqual(row["id"], 7)
        self.assertEqual(row["amount_rub"], 100)
        self.assertEqual(row["include_in_management_result"], 0)
