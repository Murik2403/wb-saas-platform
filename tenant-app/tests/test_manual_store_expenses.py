from __future__ import annotations

from datetime import date

from db.core import connect, init_db
from db.manual_expenses import (
    delete_manual_store_expense,
    read_manual_store_expenses,
    save_manual_store_expense,
    set_manual_store_expense_inclusion,
)
from db.manual_expenses import read_manual_expenses, save_manual_expense

from .base import DbTestCase


class ManualStoreExpenseTests(DbTestCase):
    def test_empty_db_has_new_store_expense_table(self) -> None:
        self.assertEqual(read_manual_store_expenses(), [])
        with connect() as conn:
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='manual_store_expenses'"
            ).fetchone()
        self.assertIsNotNone(table)

    def test_migration_does_not_change_existing_sku_rows(self) -> None:
        expense_id = save_manual_expense(
            nm_id=123,
            article="taxes",
            amount_rub=600,
            allocation_basis="total",
            source="manual",
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            note="existing SKU expense",
        )
        before = read_manual_expenses(nm_id=123)

        init_db()

        after = read_manual_expenses(nm_id=123)
        self.assertEqual(after, before)
        self.assertEqual(after[0]["id"], expense_id)

    def test_save_read_filter_include_and_delete_store_expense(self) -> None:
        expense_id = save_manual_store_expense(
            article="taxes",
            amount_rub=300,
            source="manual",
            period_start="2026-09-01",
            period_end="2026-09-30",
            note="USN",
        )
        rows = read_manual_store_expenses(start="2026-09-15", end="2026-10-01")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], expense_id)
        self.assertEqual(rows[0]["include_in_management_result"], 0)
        self.assertEqual(rows[0]["amount_rub"], 300)

        self.assertTrue(set_manual_store_expense_inclusion(expense_id, True))
        self.assertEqual(read_manual_store_expenses()[0]["include_in_management_result"], 1)
        self.assertFalse(set_manual_store_expense_inclusion(9999, True))
        self.assertTrue(delete_manual_store_expense(expense_id))
        self.assertFalse(delete_manual_store_expense(expense_id))
        self.assertEqual(read_manual_store_expenses(), [])

    def test_inclusion_update_changes_only_requested_row(self) -> None:
        first = save_manual_store_expense(
            article="taxes", amount_rub=100, source="manual",
            period_start="2026-09-01", period_end="2026-09-01",
        )
        second = save_manual_store_expense(
            article="storage", amount_rub=200, source="import",
            period_start="2026-09-02", period_end="2026-09-02",
        )

        self.assertTrue(set_manual_store_expense_inclusion(second, True))
        rows = {row["id"]: row for row in read_manual_store_expenses()}
        self.assertEqual(rows[first]["include_in_management_result"], 0)
        self.assertEqual(rows[second]["include_in_management_result"], 1)

    def test_validates_article_amount_source_period_and_commission_opt_in(self) -> None:
        valid = {
            "article": "taxes",
            "amount_rub": 1,
            "source": "manual",
            "period_start": "2026-09-01",
            "period_end": "2026-09-30",
        }
        invalid_rows = [
            {**valid, "article": "rent"},
            {**valid, "amount_rub": -1},
            {**valid, "amount_rub": None},
            {**valid, "amount_rub": float("inf")},
            {**valid, "amount_rub": float("nan")},
            {**valid, "source": "api"},
            {**valid, "period_start": "not-a-date"},
            {**valid, "period_start": "2026-09-30", "period_end": "2026-09-01"},
        ]
        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaises(ValueError):
                save_manual_store_expense(**row)

        with self.assertRaisesRegex(ValueError, "Комиссия WB"):
            save_manual_store_expense(
                article="wb_commission", amount_rub=10, source="manual",
                period_start="2026-09-01", period_end="2026-09-30",
                include_in_management_result=True,
            )

    def test_legacy_sku_rows_survive_legacy_schema_migration(self) -> None:
        with connect() as conn:
            conn.execute("DROP TABLE manual_expenses")
            conn.execute("""CREATE TABLE manual_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nm_id INTEGER NOT NULL,
                article TEXT NOT NULL, amount_rub REAL NOT NULL,
                allocation_basis TEXT NOT NULL DEFAULT 'per_unit', source TEXT NOT NULL DEFAULT 'manual',
                period_start TEXT NOT NULL, period_end TEXT NOT NULL, note TEXT DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            conn.execute("""INSERT INTO manual_expenses(
                id,nm_id,article,amount_rub,allocation_basis,source,period_start,period_end,note,created_at,updated_at
            ) VALUES (7,123,'taxes',100,'total','manual','2026-09-01','2026-09-30','old','created','updated')""")

        init_db()

        legacy = read_manual_expenses(nm_id=123)
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0]["id"], 7)
        self.assertEqual(legacy[0]["amount_rub"], 100)
        self.assertEqual(legacy[0]["note"], "old")
        self.assertEqual(legacy[0]["created_at"], "created")
        self.assertEqual(legacy[0]["updated_at"], "updated")
        self.assertEqual(legacy[0]["include_in_management_result"], 0)
        self.assertEqual(read_manual_store_expenses(), [])
