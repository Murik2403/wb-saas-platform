"""Live progress of a full sync: written per stage, never overwrites the final result."""
from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

import db.core as core
import sync
from tests.base import DbTestCase


def _row(log_id: int):
    with core.connect() as conn:
        return conn.execute("SELECT status, details FROM sync_log WHERE id=?", (log_id,)).fetchone()


class SyncProgressStorageTests(DbTestCase):
    def test_progress_is_written_while_running(self):
        log_id = core.start_sync_log()
        core.update_sync_progress(log_id, 3, 7, "sales")
        row = _row(log_id)
        self.assertEqual((row["status"], row["details"]), ("running", "progress: 3/7 sales"))

    def test_late_progress_never_overwrites_the_final_result(self):
        log_id = core.start_sync_log()
        core.finish_sync_log(log_id, "success", "orders: 5, sales: 4, financial_rows: 9")
        core.update_sync_progress(log_id, 7, 7, "ads")  # a straggler write after finish
        row = _row(log_id)
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["details"], "orders: 5, sales: 4, financial_rows: 9")


class SyncProgressFlowTests(DbTestCase):
    def run_sync(self, progress_side_effect=None, fail_orders=False):
        with ExitStack() as stack:
            names = ["clear_demo_data", "replace_product_catalog", "upsert_orders", "upsert_sales", "upsert_financial",
                     "insert_stocks", "process_sales_fifo_events", "replace_ads_period", "cleanup_orphan_zero_costs"]
            for name in names:
                stack.enter_context(mock.patch.object(sync, name, return_value={} if name == "process_sales_fifo_events" else 1))
            stack.enter_context(mock.patch.object(sync, "get_token", return_value="test-only"))
            stack.enter_context(mock.patch.object(sync, "load_settings", return_value={}))
            stack.enter_context(mock.patch.object(sync, "max_value", return_value=None))
            stack.enter_context(mock.patch.object(sync, "sales_fifo_tracking_status", return_value={"initialized": True}))
            stack.enter_context(mock.patch.object(sync, "_record_first_sync_event"))
            progress = stack.enter_context(mock.patch.object(sync, "update_sync_progress", side_effect=progress_side_effect))
            api = stack.enter_context(mock.patch.object(sync, "WBAPI")).return_value
            for name in ["get_product_cards", "get_orders", "get_sales", "get_financial_report", "get_stocks", "get_ad_stats"]:
                getattr(api, name).return_value = []
            api.get_campaign_ids.return_value = []
            if fail_orders:
                api.get_orders.side_effect = RuntimeError("WB down")
            try:
                sync.sync_all()
            except RuntimeError:
                pass
            return progress

    def test_reports_each_of_the_seven_stages_in_order(self):
        progress = self.run_sync()
        calls = [c.args[1:] for c in progress.call_args_list]  # (done, total, stage)
        self.assertEqual(calls, [(1, 7, "products"), (2, 7, "orders"), (3, 7, "sales"), (4, 7, "finance"),
                                 (5, 7, "stocks"), (6, 7, "fifo"), (7, 7, "ads")])

    def test_a_failing_progress_write_does_not_break_the_sync(self):
        self.run_sync(progress_side_effect=RuntimeError("db locked"))
        with core.connect() as conn:
            last = conn.execute("SELECT status FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(last["status"], "success")

    def test_failed_sync_stops_reporting_and_ends_in_error(self):
        progress = self.run_sync(fail_orders=True)
        self.assertEqual([c.args[3] for c in progress.call_args_list], ["products"])
        with core.connect() as conn:
            last = conn.execute("SELECT status FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(last["status"], "error")
