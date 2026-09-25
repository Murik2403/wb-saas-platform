from __future__ import annotations

import hashlib
import json

from db.core import connect, init_db
from db.manual_expenses import delete_manual_store_expense, read_manual_store_expenses, save_manual_store_expense
from db.store_expense_workflows import (
    confirm_store_expense_import,
    create_recurring_store_expense_template,
    generate_recurring_store_expenses,
    list_recurring_store_expense_templates,
    save_store_expense_import_preview,
    set_recurring_store_expense_template_active,
)
from .base import DbTestCase


class StoreExpenseWorkflowStorageTests(DbTestCase):
    def test_init_migrates_store_expense_table_missing_opt_in_and_fingerprint(self) -> None:
        expense_id = save_manual_store_expense(
            article="storage", amount_rub=25, source="manual",
            period_start="2026-09-01", period_end="2026-09-30",
        )
        with connect() as conn:
            conn.execute("DROP INDEX idx_manual_store_expenses_inclusion")
            conn.execute("DROP INDEX idx_manual_store_expenses_import_fingerprint")
            conn.execute("ALTER TABLE manual_store_expenses DROP COLUMN include_in_management_result")
            conn.execute("ALTER TABLE manual_store_expenses DROP COLUMN row_fingerprint")

        init_db()
        rows = read_manual_store_expenses()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], expense_id)
        self.assertEqual(rows[0]["include_in_management_result"], 0)
        self.assertIsNone(rows[0]["row_fingerprint"])
        with connect() as conn:
            indexes = {str(row["name"]) for row in conn.execute(
                "PRAGMA index_list(manual_store_expenses)"
            ).fetchall()}
        self.assertIn("idx_manual_store_expenses_inclusion", indexes)
        self.assertIn("idx_manual_store_expenses_import_fingerprint", indexes)

    def test_recurring_template_rejects_wb_commission(self) -> None:
        with self.assertRaisesRegex(ValueError, "Комиссия WB"):
            create_recurring_store_expense_template(
                article="wb_commission", amount_rub=100
            )

    def test_pause_resume_changes_only_selected_template_and_skips_inactive(self) -> None:
        paused_id = create_recurring_store_expense_template(
            article="storage", amount_rub=100
        )
        active_id = create_recurring_store_expense_template(
            article="taxes", amount_rub=200
        )

        self.assertTrue(set_recurring_store_expense_template_active(paused_id, False))
        self.assertFalse(set_recurring_store_expense_template_active(999999, False))
        states = {row["id"]: row["active"] for row in list_recurring_store_expense_templates()}
        self.assertEqual(states, {paused_id: 0, active_id: 1})
        generated = generate_recurring_store_expenses(year=2026, month=10)
        self.assertEqual(generated["created"], 1)
        expense = read_manual_store_expenses()[0]
        self.assertEqual(expense["article"], "taxes")

        self.assertTrue(set_recurring_store_expense_template_active(paused_id, True))
        self.assertTrue(set_recurring_store_expense_template_active(active_id, False))
        states = {row["id"]: row["active"] for row in list_recurring_store_expense_templates()}
        self.assertEqual(states, {paused_id: 1, active_id: 0})

    def test_deleting_recurring_expense_keeps_cancelled_month_tombstone(self) -> None:
        template_id = create_recurring_store_expense_template(
            article="storage", amount_rub=15000, note="Склад"
        )
        first = generate_recurring_store_expenses(year=2026, month=9)
        self.assertEqual(first["created"], 1)
        expense_id = first["expense_ids"][0]

        self.assertTrue(delete_manual_store_expense(expense_id))
        self.assertFalse(delete_manual_store_expense(expense_id))
        self.assertEqual(read_manual_store_expenses(), [])
        with connect() as conn:
            occurrence = conn.execute(
                """SELECT template_id,period_start,expense_id,status
                     FROM recurring_store_expense_occurrences"""
            ).fetchone()
            indexes = conn.execute("PRAGMA foreign_key_list(recurring_store_expense_occurrences)").fetchall()
        self.assertEqual(occurrence["template_id"], template_id)
        self.assertEqual(occurrence["period_start"], "2026-09-01")
        self.assertIsNone(occurrence["expense_id"])
        self.assertEqual(occurrence["status"], "cancelled")
        self.assertEqual([row["table"] for row in indexes], ["recurring_store_expense_templates"])

        same_month = generate_recurring_store_expenses(year=2026, month=9)
        self.assertEqual(same_month["created"], 0)
        self.assertEqual(same_month["already_exists"], 1)
        self.assertEqual(same_month["expense_ids"], [])
        self.assertEqual(read_manual_store_expenses(), [])

        next_month = generate_recurring_store_expenses(year=2026, month=10)
        self.assertEqual(next_month["created"], 1)
        rows = read_manual_store_expenses()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["period_start"], "2026-10-01")

    def test_init_migrates_legacy_occurrence_schema_for_tombstones(self) -> None:
        template_id = create_recurring_store_expense_template(article="storage", amount_rub=100)
        expense_id = save_manual_store_expense(
            article="storage", amount_rub=100, source="manual",
            period_start="2026-09-01", period_end="2026-09-30",
        )
        with connect() as conn:
            conn.execute("DROP TABLE recurring_store_expense_occurrences")
            conn.execute("""CREATE TABLE recurring_store_expense_occurrences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                expense_id INTEGER NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                UNIQUE(template_id,period_start),
                FOREIGN KEY(template_id) REFERENCES recurring_store_expense_templates(id) ON DELETE RESTRICT,
                FOREIGN KEY(expense_id) REFERENCES manual_store_expenses(id) ON DELETE RESTRICT
            )""")
            conn.execute(
                "INSERT INTO recurring_store_expense_occurrences(template_id,period_start,expense_id,created_at) VALUES (?,?,?,?)",
                (template_id, "2026-09-01", expense_id, "created"),
            )

        init_db()
        self.assertTrue(delete_manual_store_expense(expense_id))
        with connect() as conn:
            occurrence = conn.execute(
                "SELECT expense_id,status FROM recurring_store_expense_occurrences"
            ).fetchone()
        self.assertIsNone(occurrence["expense_id"])
        self.assertEqual(occurrence["status"], "cancelled")

    def test_monthly_recurring_spans_calendar_month_and_is_idempotent(self) -> None:
        storage_template_id = create_recurring_store_expense_template(
            article="storage", amount_rub=1250, note="Склад"
        )
        tax_template_id = create_recurring_store_expense_template(
            article="taxes", amount_rub=500, active=True
        )
        inactive_template_id = create_recurring_store_expense_template(
            article="advertising", amount_rub=100, active=False
        )

        feb = generate_recurring_store_expenses(year=2028, month=2)
        self.assertEqual(feb["created"], 2)
        self.assertEqual(feb["already_exists"], 0)
        self.assertEqual(len(feb["expense_ids"]), 2)
        rows = read_manual_store_expenses()
        by_article = {row["article"]: row for row in rows}
        self.assertEqual(by_article["storage"]["period_start"], "2028-02-01")
        self.assertEqual(by_article["storage"]["period_end"], "2028-02-29")
        self.assertEqual(by_article["taxes"]["period_start"], "2028-02-01")
        self.assertEqual(by_article["taxes"]["period_end"], "2028-02-29")
        self.assertEqual(by_article["storage"]["source"], "manual")
        self.assertEqual(by_article["storage"]["include_in_management_result"], 0)

        repeated = generate_recurring_store_expenses(year=2028, month=2)
        self.assertEqual(repeated["created"], 0)
        self.assertEqual(repeated["already_exists"], 2)
        self.assertEqual(repeated["expense_ids"], feb["expense_ids"])

        templates = list_recurring_store_expense_templates()
        self.assertEqual({t["id"] for t in templates}, {storage_template_id, tax_template_id, inactive_template_id})

    def test_preview_preserves_invalid_rows_and_identical_hash_is_same_batch(self) -> None:
        rows = [
            {"line_number": 2, "article": "storage", "amount_rub": 10,
             "period_start": "2026-09-01", "period_end": "2026-09-30", "note": "ok",
             "errors": [], "duplicate": False},
            {"line_number": 3, "article": "unknown", "amount_rub": None,
             "period_start": "bad", "period_end": "2026-09-01", "note": "bad",
             "errors": ["unsupported article", "invalid amount", "invalid period"], "duplicate": False},
        ]
        digest = hashlib.sha256(b"fixture csv").hexdigest()
        first = save_store_expense_import_preview(content_sha256=digest, rows=rows)
        self.assertFalse(first["already_imported"])
        self.assertEqual(len(first["rows"]), 2)
        self.assertTrue(set(rows[1]["errors"]).issubset(first["rows"][1]["errors"]))
        self.assertEqual(first["valid_count"], 1)
        self.assertEqual(first["invalid_count"], 1)
        self.assertEqual(read_manual_store_expenses(), [])

        second = save_store_expense_import_preview(content_sha256=digest, rows=[])
        self.assertFalse(second["already_imported"])
        self.assertEqual(second["batch_id"], first["batch_id"])
        self.assertEqual(second["rows"], first["rows"])
        confirmed = confirm_store_expense_import(second["batch_id"])
        self.assertEqual(confirmed["created_count"], 1)
        after_confirmation = save_store_expense_import_preview(content_sha256=digest, rows=[])
        self.assertTrue(after_confirmation["already_imported"])

    def test_confirmation_imports_valid_rows_once_excluded_and_blocks_commission(self) -> None:
        digest = hashlib.sha256(b"valid import").hexdigest()
        preview = save_store_expense_import_preview(
            content_sha256=digest,
            rows=[
                {"line_number": 2, "article": "storage", "amount_rub": 250,
                 "period_start": "2026-09-01", "period_end": "2026-09-30", "note": "rent",
                 "errors": [], "duplicate": False},
                {"line_number": 3, "article": "wb_commission", "amount_rub": 900,
                 "period_start": "2026-09-01", "period_end": "2026-09-30", "note": "WB",
                 "errors": [], "duplicate": False},
                {"line_number": 4, "article": "taxes", "amount_rub": 1,
                 "period_start": "2026-09-01", "period_end": "2026-09-30", "note": "duplicate",
                 "errors": [], "duplicate": True},
                {"line_number": 5, "article": "taxes", "amount_rub": None,
                 "period_start": "bad", "period_end": "2026-09-30", "note": "invalid",
                 "errors": ["invalid amount"], "duplicate": False},
            ],
        )
        confirmed = confirm_store_expense_import(preview["batch_id"])
        self.assertEqual(confirmed["created_count"], 1)
        self.assertFalse(confirmed["already_confirmed"])
        self.assertEqual(len(confirmed["expense_ids"]), 1)
        rows = read_manual_store_expenses()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["article"], "storage")
        self.assertEqual(rows[0]["source"], "import")
        self.assertEqual(rows[0]["include_in_management_result"], 0)

        again = confirm_store_expense_import(preview["batch_id"])
        self.assertEqual(again["created_count"], confirmed["created_count"])
        self.assertEqual(again["expense_ids"], confirmed["expense_ids"])
        self.assertTrue(again["already_confirmed"])
        self.assertEqual(len(read_manual_store_expenses()), 1)
        with connect() as conn:
            batch = conn.execute(
                "SELECT status, result_json FROM store_expense_import_batches WHERE id=?",
                (preview["batch_id"],),
            ).fetchone()
        self.assertEqual(batch["status"], "confirmed")
        self.assertEqual(json.loads(batch["result_json"]), confirmed)

    def test_schema_enforces_one_occurrence_and_hash(self) -> None:
        create_recurring_store_expense_template(article="storage", amount_rub=10)
        with connect() as conn:
            occurrence_indexes = {
                row["name"] for row in conn.execute("PRAGMA index_list(recurring_store_expense_occurrences)")
            }
            batch_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(store_expense_import_batches)")
            }
        self.assertTrue(occurrence_indexes)
        self.assertIn("content_sha256", batch_columns)

    def test_different_file_cannot_reimport_same_normalized_row(self) -> None:
        row = {"line_number": 2, "article": "storage", "amount_rub": 250,
               "period_start": "2026-09-01", "period_end": "2026-09-30", "note": "rent",
               "errors": [], "duplicate": False}
        first = save_store_expense_import_preview(
            content_sha256=hashlib.sha256(b"file one").hexdigest(), rows=[row]
        )
        result = confirm_store_expense_import(first["batch_id"])
        self.assertEqual(result["created_count"], 1)
        second = save_store_expense_import_preview(
            content_sha256=hashlib.sha256(b"different file").hexdigest(), rows=[row]
        )
        self.assertTrue(second["rows"][0]["duplicate"])
        result2 = confirm_store_expense_import(second["batch_id"])
        self.assertEqual(result2["created_count"], 0)
        self.assertEqual(len(read_manual_store_expenses()), 1)
