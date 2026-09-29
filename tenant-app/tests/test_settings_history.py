from __future__ import annotations
import json
import sqlite3
from unittest.mock import patch
import pandas as pd
from db.core import connect, init_db
from db.fifo_materials import save_costs
from db.production import save_production_settings
from .base import DbTestCase


class SettingsHistoryTests(DbTestCase):
    def costs(self, value=10, **kwargs):
        save_costs(pd.DataFrame([{"nm_id": 100, "cost_per_wb_unit": value, "supplier_article": "A", "note": "secret-do-not-log"}]), **kwargs)

    def norms(self, value=2, **kwargs):
        save_production_settings(pd.DataFrame([{"nm_id": 100, "enabled": True, "material_per_unit": value, "target_days": 21, "min_batch": 1, "material_name": "Canvas", "note": "secret-do-not-log"}]), **kwargs)

    def events(self):
        with connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM settings_change_history ORDER BY id")]

    def test_cost_creation_change_noop_and_metadata(self):
        self.costs(source="tenant_api", actor="account-1")
        self.costs(10)
        self.costs(12)
        events = self.events()
        self.assertEqual(len(events), 2)
        self.assertIsNone(events[0]["before_json"])
        self.assertEqual(events[0]["actor"], "account-1")
        self.assertEqual(events[0]["source"], "tenant_api")
        self.assertEqual(json.loads(events[1]["before_json"])["cost_per_wb_unit"], 10)
        self.assertEqual(json.loads(events[1]["after_json"])["cost_per_wb_unit"], 12)
        self.assertNotIn("secret-do-not-log", str(events))

    def test_norm_creation_change_noop_typed_values(self):
        self.norms()
        self.norms()
        self.norms(3)
        events = self.events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["source"], "legacy")
        self.assertIsNone(events[0]["actor"])
        self.assertIs(json.loads(events[0]["after_json"])["enabled"], True)
        self.assertEqual(json.loads(events[1]["before_json"])["material_per_unit"], 2)
        self.assertEqual(json.loads(events[1]["after_json"])["material_per_unit"], 3)

    def test_cost_batch_journal_failure_rolls_back_all_rows(self):
        self.costs()
        with patch("db.settings_history.append_settings_change", side_effect=sqlite3.OperationalError("journal unavailable")):
            with self.assertRaises(sqlite3.OperationalError):
                save_costs(pd.DataFrame([{"nm_id": 200, "cost_per_wb_unit": 15}, {"nm_id": 100, "cost_per_wb_unit": 20}]))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT cost_per_wb_unit FROM costs WHERE nm_id=100").fetchone()[0], 10)
            self.assertIsNone(conn.execute("SELECT * FROM costs WHERE nm_id=200").fetchone())
        self.assertEqual(len(self.events()), 1)

    def test_norm_journal_failure_rolls_back_mutation(self):
        self.norms()
        with patch("db.settings_history.append_settings_change", side_effect=sqlite3.OperationalError("journal unavailable")):
            with self.assertRaises(sqlite3.OperationalError):
                self.norms(8)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT material_per_unit FROM production_settings WHERE nm_id=100").fetchone()[0], 2)
        self.assertEqual(len(self.events()), 1)

    def test_migration_idempotent_no_fabricated_history(self):
        with connect() as conn:
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES(100,10)")
        init_db()
        init_db()
        self.assertEqual(self.events(), [])

    def test_history_update_delete_rejected(self):
        self.costs()
        for sql in ["UPDATE settings_change_history SET source='bad'", "DELETE FROM settings_change_history"]:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                with connect() as conn:
                    conn.execute(sql)
        self.assertEqual(len(self.events()), 1)

    def test_transaction_lock_precedes_old_value_read(self):
        self.costs()
        statements = []
        from db import core
        original = core.connect
        from contextlib import contextmanager
        @contextmanager
        def traced():
            with original() as conn:
                conn.set_trace_callback(statements.append)
                yield conn
        with patch("db.core.connect", traced):
            self.costs(15)
        begin = next(i for i,s in enumerate(statements) if "BEGIN IMMEDIATE" in s)
        read = next(i for i,s in enumerate(statements) if "SELECT * FROM costs" in s)
        self.assertLess(begin, read)

    def test_second_batch_journal_insert_failure_rolls_back_first_row_and_event(self):
        with connect() as conn:
            conn.executescript("CREATE TRIGGER history_fail_second BEFORE INSERT ON settings_change_history WHEN NEW.nm_id=200 BEGIN SELECT RAISE(ABORT, 'second event blocked'); END;")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "second event blocked"):
            save_costs(pd.DataFrame([{"nm_id": 100, "cost_per_wb_unit": 15}, {"nm_id": 200, "cost_per_wb_unit": 20}]))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM costs WHERE nm_id IN (100,200)").fetchone()[0], 0)
        self.assertEqual(self.events(), [])

    def test_cost_nonfinite_snapshot_rejected_without_mutation(self):
        self.costs()
        with self.assertRaisesRegex(ValueError, "finite"):
            self.costs(float("inf"))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT cost_per_wb_unit FROM costs WHERE nm_id=100").fetchone()[0], 10)
        self.assertEqual(len(self.events()), 1)

    def test_concurrent_writers_have_unbroken_old_new_chain(self):
        from concurrent.futures import ThreadPoolExecutor
        self.costs(10)
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(self.costs, [11, 12, 13, 14]))
        events = self.events()
        self.assertEqual(len(events), 5)
        for previous, current in zip(events, events[1:]):
            self.assertEqual(json.loads(previous["after_json"]), json.loads(current["before_json"]))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT cost_per_wb_unit FROM costs WHERE nm_id=100").fetchone()[0], json.loads(events[-1]["after_json"])["cost_per_wb_unit"])

    def auto_costs(self, confirmed=True, labor=5):
        from db.fifo_materials import save_auto_cost_settings
        save_auto_cost_settings(pd.DataFrame([{"nm_id": 100, "packaging_cost_rub": 0, "labor_cost_rub": labor, "other_cost_rub": 3, "packaging_cost_confirmed": confirmed}]))

    def test_auto_settings_zero_confirmation_change_and_noop(self):
        self.auto_costs(False)
        self.auto_costs(True)
        self.auto_costs(True)
        self.auto_costs(True, labor=9)
        events = self.events()
        self.assertEqual(len(events), 3)
        before, after = json.loads(events[1]["before_json"]), json.loads(events[1]["after_json"])
        self.assertEqual(before["packaging_cost_status"], "unknown")
        self.assertEqual(after["packaging_cost_status"], "confirmed")
        self.assertEqual(after["packaging_cost_source"], "manual")
        self.assertEqual(after["packaging_cost_rub"], 0)
        self.assertEqual(json.loads(events[2]["after_json"])["labor_cost_rub"], 9)

    def test_apply_forecast_journals_current_cost_and_material(self):
        from db.fifo_materials import apply_forecast_costs
        self.auto_costs()
        with connect() as conn:
            conn.execute("UPDATE costs SET forecast_total_cost_rub=22,forecast_material_cost_rub=14 WHERE nm_id=100")
        frame = pd.DataFrame([{"nm_id": 100, "apply_forecast": True}])
        self.assertEqual(apply_forecast_costs(frame, source="legacy", actor="owner"), 1)
        self.assertEqual(apply_forecast_costs(frame), 1)
        events = self.events()
        self.assertEqual(len(events), 2)
        self.assertEqual(json.loads(events[-1]["before_json"])["cost_per_wb_unit"], 0)
        self.assertEqual(json.loads(events[-1]["after_json"])["cost_per_wb_unit"], 22)
        self.assertEqual(json.loads(events[-1]["after_json"])["material_cost_rub"], 14)
        self.assertEqual(events[-1]["actor"], "owner")

    def test_auto_settings_journal_failure_rolls_back(self):
        self.auto_costs()
        with patch("db.settings_history.append_settings_change", side_effect=sqlite3.OperationalError("failure")):
            with self.assertRaises(sqlite3.OperationalError):
                self.auto_costs(labor=19)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT labor_cost_rub FROM costs WHERE nm_id=100").fetchone()[0], 5)
        self.assertEqual(len(self.events()), 1)

    def test_apply_forecast_journal_failure_rolls_back(self):
        from db.fifo_materials import apply_forecast_costs
        self.auto_costs()
        with connect() as conn:
            conn.execute("UPDATE costs SET forecast_total_cost_rub=22,forecast_material_cost_rub=14 WHERE nm_id=100")
        with patch("db.settings_history.append_settings_change", side_effect=sqlite3.OperationalError("failure")):
            with self.assertRaises(sqlite3.OperationalError):
                apply_forecast_costs(pd.DataFrame([{"nm_id": 100, "apply_forecast": True}]))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT cost_per_wb_unit FROM costs WHERE nm_id=100").fetchone()[0], 0)
        self.assertEqual(len(self.events()), 1)

    def test_explicit_insert_or_replace_cannot_overwrite_history(self):
        self.costs()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with connect() as conn:
                conn.execute("INSERT OR REPLACE INTO settings_change_history SELECT id,kind,nm_id,before_json,after_json,changed_at,'bad',actor FROM settings_change_history")
        self.assertEqual(self.events()[0]["source"], "legacy")

    def test_confirmed_zero_packaging_survives_forecast_refresh(self):
        from db.fifo_materials import refresh_auto_costs
        self.norms(2)
        with patch("db.fifo_materials._material_rate_maps", return_value=({"canvas": 10}, 10, {"canvas": "PO"}, "PO")):
            self.auto_costs(True)
            event_count = len(self.events())
            refresh_auto_costs()
            refresh_auto_costs()
        with connect() as conn:
            row = conn.execute("SELECT packaging_cost_rub,forecast_total_cost_rub FROM costs WHERE nm_id=100").fetchone()
        self.assertEqual(row["packaging_cost_rub"], 0)
        self.assertEqual(row["forecast_total_cost_rub"], 28)
        self.assertEqual(len(self.events()), event_count)

    def test_unknown_zero_packaging_retains_default_and_journals_refresh(self):
        self.norms(2)
        with patch("db.fifo_materials._material_rate_maps", return_value=({"canvas": 10}, 10, {"canvas": "PO"}, "PO")):
            self.auto_costs(False)
        with connect() as conn:
            row = conn.execute("SELECT packaging_cost_rub,forecast_total_cost_rub FROM costs WHERE nm_id=100").fetchone()
        self.assertEqual(row["packaging_cost_rub"], 12)
        self.assertEqual(row["forecast_total_cost_rub"], 40)
        event = self.events()[-1]
        self.assertEqual(event["source"], "forecast_refresh")
        self.assertEqual(json.loads(event["before_json"])["packaging_cost_rub"], 0)
        self.assertEqual(json.loads(event["after_json"])["packaging_cost_rub"], 12)
