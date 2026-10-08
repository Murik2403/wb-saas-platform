import os
import subprocess
import sys
from tests.base import DbTestCase
from db.core import connect
from db.ingest import _stable_synthetic_id, upsert_orders, upsert_sales


class StableIngestIdentityTests(DbTestCase):
    def test_process_seed_and_mapping_order_do_not_change_id(self):
        row = {"nmId": 10, "date": "2026-09-01", "totalPrice": 250}
        self.assertEqual(_stable_synthetic_id(row), _stable_synthetic_id(dict(reversed(list(row.items())))))
        code = "from db.ingest import _stable_synthetic_id; print(_stable_synthetic_id(" + repr(row) + "))"
        values = [subprocess.check_output([sys.executable, "-c", code], env={**os.environ, "PYTHONHASHSEED": seed}, text=True).strip() for seed in ("1", "93")]
        self.assertEqual(values[0], values[1])

    def test_repeated_fallback_sync_does_not_duplicate_orders_or_sales(self):
        row = {"nmId": 10, "date": "2026-09-01", "totalPrice": 250}
        for fn, table in ((upsert_orders, "orders"), (upsert_sales, "sales")):
            fn([row]); fn([dict(reversed(list(row.items())))])
            with connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 1)

    def test_marketplace_identifiers_are_preserved(self):
        upsert_orders([{"srid": "official-order", "date": "2026-09-01"}])
        upsert_sales([{"saleID": "official-sale", "date": "2026-09-01"}])
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT id FROM orders").fetchone()[0], "official-order")
            self.assertEqual(conn.execute("SELECT id FROM sales").fetchone()[0], "official-sale")

    def test_existing_legacy_row_is_reused_without_deleting_history(self):
        row = {"nmId": 10, "date": "2026-09-01", "totalPrice": 250}
        for fn, table in ((upsert_orders, "orders"), (upsert_sales, "sales")):
            fn([row])
            with connect() as conn:
                conn.execute("UPDATE " + table + " SET id=?", ("10::2026-09-01:-123456",))
            fn([row])
            with connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT id FROM " + table).fetchone()[0], "10::2026-09-01:-123456")
