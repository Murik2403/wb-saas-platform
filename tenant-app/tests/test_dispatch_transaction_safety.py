"""Real SQLite concurrency and rollback checks for dispatch document posting."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, local
from unittest.mock import patch

from db.core import connect
from db.fifo_finished_goods import _create_finished_goods_layer, initialize_finished_goods_fifo
from db.inventory_movements import post_dispatches, post_wb_receipts
from tests.base import DbTestCase


class DispatchTransactionSafetyTests(DbTestCase):
    def test_concurrent_fifo_initialization_does_not_create_duplicate_opening(self):
        with connect() as conn:
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) "
                "VALUES(100,1,10,0,0)"
            )
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES(100,10)")
        from db.fifo_finished_goods import _finished_layer_totals
        start, stale_read = Barrier(2), Barrier(2)
        first_committed, worker = Event(), local()

        def totals(conn, nm_id, location, chrt_id=0):
            # register_valuation starts a deferred read transaction itself.
            # Capture the boundary before it does so: the new writer must
            # already own its transaction before observing the old layer sum.
            already_locked = conn.in_transaction
            value = _finished_layer_totals(conn, nm_id, location, chrt_id)
            if location == "ready" and not already_locked:
                stale_read.wait(timeout=5)
                if worker.index == 1 and not first_committed.wait(timeout=5):
                    raise AssertionError("first initializer did not complete")
            return value

        def run(index):
            worker.index = index
            start.wait(timeout=5)
            try:
                return initialize_finished_goods_fifo()
            finally:
                if index == 0:
                    first_committed.set()

        with patch("db.fifo_finished_goods._finished_layer_totals", side_effect=totals):
            with ThreadPoolExecutor(max_workers=2) as workers:
                futures = [workers.submit(run, index) for index in range(2)]
                results = [future.result(timeout=10) for future in futures]
        self.assertEqual(sum(r["created"] for r in results), 1, results)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT SUM(ready_units),SUM(original_amount_rub) FROM finished_goods_cost_layers").fetchone()[:], (10, 100.0))
            self.assertEqual(conn.execute("SELECT ready_units FROM product_pipeline").fetchone()[0], 10)

    def seed_dispatch(self, key="dispatch-1", units=5, ready=20):
        with connect() as conn:
            conn.execute(
                "INSERT INTO product_pipeline(nm_id,local_known,ready_units,inbound_known,inbound_units) "
                "VALUES(100,1,?,1,0)", (ready,),
            )
            _create_finished_goods_layer(
                conn, 100, "SKU", "Product", "opening", "seed", "2026-01-01", ready, 10, "ready",
            )
            self.add_task(conn, key, units)

    def add_task(self, conn, key, units):
        conn.execute(
            "INSERT INTO execution_tasks(task_key,task_type,nm_id,supplier_article,product_name,"
            "actual_units,status,route,dispatch_date,expected_arrival_date) "
            "VALUES(?,'dispatch',100,'SKU','Product',?,'Отгружено','Стандартная FBO','2026-01-02','2026-01-03')",
            (key, units),
        )

    def concurrent_post(self, post, movement_type):
        """Force stale readers to rendezvous if the idempotency check isn't locked.

        A correctly serialized writer already owns its transaction and must not
        rendezvous inside it, since the other writer is waiting for its lock.
        """
        from db.production import _active_movement
        start = Barrier(2)
        stale_read = Barrier(2)
        first_committed = Event()
        worker = local()

        def active(conn, kind, key):
            value = _active_movement(conn, kind, key)
            if kind == movement_type and not conn.in_transaction:
                stale_read.wait(timeout=5)
                # The second request resumes with its old "not posted" result
                # only after the first has committed. This avoids relying on a
                # nondeterministic SQLite lock-upgrade error to expose the bug.
                if worker.index == 1 and not first_committed.wait(timeout=5):
                    raise AssertionError("first writer did not complete")
            return value

        def run(index):
            worker.index = index
            start.wait(timeout=5)
            try:
                return post(["dispatch-1"])
            finally:
                if index == 0:
                    first_committed.set()

        with patch("db.production._active_movement", side_effect=active):
            with ThreadPoolExecutor(max_workers=2) as workers:
                futures = [workers.submit(run, index) for index in range(2)]
                return [future.result(timeout=10) for future in futures]

    def test_same_dispatch_two_writers_posts_once(self):
        self.seed_dispatch()
        results = self.concurrent_post(post_dispatches, "dispatch")
        self.assertEqual(sum(r["posted"] for r in results), 1, results)
        self.assertEqual(sum(r["skipped"] for r in results), 1, results)
        self.assertFalse([error for r in results for error in r["errors"]])
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT ready_units,inbound_units FROM product_pipeline").fetchone()[:], (15, 5))
            self.assertEqual(conn.execute("SELECT COUNT(*),SUM(goods_cost_rub) FROM inventory_movements").fetchone()[:], (1, 50.0))
            self.assertEqual(conn.execute("SELECT ready_units,inbound_units,wb_units FROM finished_goods_cost_layers").fetchone()[:], (15, 5, 0))

    def test_same_receipt_two_writers_cannot_consume_other_dispatch_stock(self):
        self.seed_dispatch()
        with connect() as conn:
            self.add_task(conn, "dispatch-2", 5)
        self.assertEqual(post_dispatches()["posted"], 2)
        with connect() as conn:
            conn.execute("UPDATE execution_tasks SET status='Принято WB' WHERE task_key='dispatch-1'")
        # Reading the referenced dispatch is the final state check before the
        # receipt starts writing; freeze its "open" state for both writers.
        results = self.concurrent_post(post_wb_receipts, "dispatch")
        self.assertEqual(sum(r["posted"] for r in results), 1, results)
        self.assertEqual(sum(r["skipped"] for r in results), 1, results)
        self.assertFalse([error for r in results for error in r["errors"]])
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT ready_units,inbound_units FROM product_pipeline").fetchone()[:], (10, 5))
            self.assertEqual(conn.execute("SELECT ready_units,inbound_units,wb_units FROM finished_goods_cost_layers").fetchone()[:], (10, 5, 5))
            self.assertEqual(conn.execute("SELECT COUNT(*),SUM(goods_cost_rub) FROM inventory_movements WHERE movement_type='wb_receipt'").fetchone()[:], (1, 50.0))
            self.assertEqual(conn.execute("SELECT status FROM inventory_movements WHERE source_task_key='dispatch-2'").fetchone()[0], "open")

    def test_dispatch_failure_rolls_back_all_then_retry_posts_once(self):
        self.seed_dispatch()
        from db.fifo_finished_goods import _move_finished_goods_fifo

        def fail_after_allocating(*args, **kwargs):
            _move_finished_goods_fifo(*args, **kwargs)
            raise ValueError("simulated failure after allocation")

        with patch("db.fifo_finished_goods._move_finished_goods_fifo", side_effect=fail_after_allocating):
            result = post_dispatches(["dispatch-1"])
        self.assertEqual(result["posted"], 0)
        self.assertEqual(len(result["errors"]), 1)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT ready_units,inbound_units FROM product_pipeline").fetchone()[:], (20, 0))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM finished_goods_fifo_allocations").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT ready_units,inbound_units FROM finished_goods_cost_layers").fetchone()[:], (20, 0))
        self.assertEqual(post_dispatches(["dispatch-1"])["posted"], 1)
        self.assertEqual(post_dispatches(["dispatch-1"])["skipped"], 1)

    def test_receipt_failure_rolls_back_all_then_retry_posts_once(self):
        self.seed_dispatch()
        self.assertEqual(post_dispatches()["posted"], 1)
        with connect() as conn:
            conn.execute("UPDATE execution_tasks SET status='Принято WB'")
        from db.fifo_finished_goods import _move_dispatch_allocation_to_wb

        def fail_after_allocating(*args, **kwargs):
            _move_dispatch_allocation_to_wb(*args, **kwargs)
            raise ValueError("simulated failure after receipt allocation")

        with patch("db.fifo_finished_goods._move_dispatch_allocation_to_wb", side_effect=fail_after_allocating):
            result = post_wb_receipts(["dispatch-1"])
        self.assertEqual(result["posted"], 0)
        self.assertEqual(len(result["errors"]), 1)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT ready_units,inbound_units FROM product_pipeline").fetchone()[:], (15, 5))
            self.assertEqual(conn.execute("SELECT status FROM inventory_movements").fetchone()[0], "open")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM finished_goods_fifo_allocations").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT ready_units,inbound_units,wb_units FROM finished_goods_cost_layers").fetchone()[:], (15, 5, 0))
        self.assertEqual(post_wb_receipts(["dispatch-1"])["posted"], 1)
        self.assertEqual(post_wb_receipts(["dispatch-1"])["skipped"], 1)
