"""Exact residual money for new ordinary layers, with legacy compatibility."""
from db.core import connect
from db.finished_history_import import physical_amount, register_valuation
from db.fifo_finished_goods import (
    _create_finished_goods_layer, _move_finished_goods_fifo,
    _move_dispatch_allocation_to_wb, _reverse_finished_goods_allocations,
)
from db.fifo_sales import _restore_return_to_original_layers
from unittest.mock import patch
from tests.base import DbTestCase


class FinishedGoodsMoneyBasisTests(DbTestCase):
    def layer(self, conn, location="wb", chrt_id=0):
        return _create_finished_goods_layer(conn, 100, "SKU", "Product", "production", "test:money", "2026-01-01", 3, .3367, location, chrt_id=chrt_id)

    def movement(self, conn, key, reference=None):
        return conn.execute(
            "INSERT INTO inventory_movements(event_key,movement_type,nm_id,movement_date,created_at,reference_movement_id) "
            "VALUES(?,'test',100,'2026-01-02','now',?)", (key, reference),
        ).lastrowid

    def read_value(self, conn, layer_id, location):
        register_valuation(conn)
        return conn.execute(
            f"SELECT finished_location_amount(id,?,{location}_units,unit_cost_rub) "
            "FROM finished_goods_cost_layers WHERE id=?", (location, layer_id),
        ).fetchone()[0]

    def test_fragmented_sales_conserve_source_and_read_residual(self):
        with connect() as conn:
            lid = self.layer(conn)
            self.assertEqual(self.read_value(conn, lid, "wb"), 1.01)
            amounts = []
            for index, residual in enumerate((.67, .33, 0.0)):
                amounts.append(_move_finished_goods_fifo(conn, self.movement(conn, str(index)), 100, "SKU", "Product", 1, "wb", None)[0])
                self.assertEqual(self.read_value(conn, lid, "wb"), residual)
            self.assertEqual(amounts, [.34, .34, .33])
            self.assertEqual(round(sum(amounts), 2), 1.01)

    def test_transfers_preserve_money_then_returns_and_reversal_restore_exact_cost(self):
        with connect() as conn:
            lid = self.layer(conn, "ready", 111)
            dispatches = []
            for i in range(3):
                dispatch = self.movement(conn, "dispatch" + str(i)); dispatches.append(dispatch)
                self.assertEqual(_move_finished_goods_fifo(conn, dispatch, 100, "SKU", "Product", 1, "ready", "inbound", 111)[0], [.34, .34, .33][i])
            for i in (2, 0, 1):
                receipt = self.movement(conn, "receipt" + str(i), dispatches[i])
                self.assertEqual(_move_dispatch_allocation_to_wb(conn, receipt, dispatches[i], 1)[0], [.34, .34, .33][i])
            self.assertEqual(self.read_value(conn, lid, "wb"), 1.01)
            self.assertEqual(self.read_value(conn, lid, "inbound"), 0.0)
            sale = self.movement(conn, "sale")
            self.assertEqual(_move_finished_goods_fifo(conn, sale, 100, "SKU", "Product", 1, "wb", None, 111)[0], .34)
            returned = self.movement(conn, "return")
            self.assertEqual(_restore_return_to_original_layers(conn, returned, {"movement_id": sale}, 1), (.34, 1))
            self.assertEqual(self.read_value(conn, lid, "wb"), 1.01)
            _reverse_finished_goods_allocations(conn, returned)
            self.assertEqual(self.read_value(conn, lid, "wb"), .67)
            _reverse_finished_goods_allocations(conn, sale)
            self.assertEqual(self.read_value(conn, lid, "wb"), 1.01)

    def test_old_layers_and_recorded_allocations_are_not_repriced(self):
        with connect() as conn:
            lid = conn.execute(
                "INSERT INTO finished_goods_cost_layers(nm_id,source_type,source_ref,source_date,original_units,wb_units,"
                "unit_cost_rub,original_amount_rub,status,created_at,updated_at) "
                "VALUES(100,'opening','legacy','2026-01-01',3,3,.3367,1.01,'active','old','old')"
            ).lastrowid
            sale = self.movement(conn, "legacy-sale")
            self.assertEqual(_move_finished_goods_fifo(conn, sale, 100, "SKU", "Product", 1, "wb", None)[0], .34)
            self.assertIsNone(physical_amount(conn, lid, "wb", 1))
            self.assertAlmostEqual(self.read_value(conn, lid, "wb"), .6734)
            self.assertEqual(conn.execute("SELECT amount_rub FROM finished_goods_fifo_allocations WHERE movement_id=?", (sale,)).fetchone()[0], .34)

    def test_variant_bases_remain_separate(self):
        with connect() as conn:
            small = self.layer(conn, chrt_id=111)
            other = _create_finished_goods_layer(conn, 100, "SKU", "Other", "production", "other", "2026-01-01", 2, .515, "wb", chrt_id=222)
            self.assertEqual(_move_finished_goods_fifo(conn, self.movement(conn, "small"), 100, "SKU", "Product", 1, "wb", None, 111)[0], .34)
            self.assertEqual(self.read_value(conn, small, "wb"), .67)
            self.assertEqual(self.read_value(conn, other, "wb"), 1.03)

    def test_held_units_keep_their_share_of_residual_money(self):
        with connect() as conn:
            lid = self.layer(conn)
            with patch("db.fifo_finished_goods.held_units", return_value=1):
                cost, _ = _move_finished_goods_fifo(conn, self.movement(conn, "free"), 100, "SKU", "Product", 2, "wb", None)
            self.assertEqual(cost, .67)
            self.assertEqual(self.read_value(conn, lid, "wb"), .34)

    def test_partial_return_then_resale_conserves_net_disposal(self):
        with connect() as conn:
            lid = self.layer(conn)
            sale = self.movement(conn, "sale-two")
            sold, _ = _move_finished_goods_fifo(conn, sale, 100, "SKU", "Product", 2, "wb", None)
            self.assertEqual(sold, .67)
            returned, restored = _restore_return_to_original_layers(conn, self.movement(conn, "partial-return"), {"movement_id": sale}, 1)
            self.assertEqual((returned, restored), (.34, 1))
            self.assertEqual(self.read_value(conn, lid, "wb"), .68)
            resale = _move_finished_goods_fifo(conn, self.movement(conn, "resale"), 100, "SKU", "Product", 1, "wb", None)[0]
            last = _move_finished_goods_fifo(conn, self.movement(conn, "last-sale"), 100, "SKU", "Product", 1, "wb", None)[0]
            self.assertEqual(round(sold - returned + resale + last, 2), 1.01)
            self.assertEqual(self.read_value(conn, lid, "wb"), 0.0)

    def test_basis_is_immutable_and_reads_do_not_create_schema(self):
        with connect() as conn:
            register_valuation(conn)
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='finished_goods_money_basis'").fetchone())
            lid = self.layer(conn)
            for sql in (
                "UPDATE finished_goods_money_basis SET initial_cents=100",
                "DELETE FROM finished_goods_money_basis",
                "INSERT OR REPLACE INTO finished_goods_money_basis SELECT * FROM finished_goods_money_basis",
            ):
                with self.assertRaisesRegex(Exception, "immutable"):
                    conn.execute(sql)
            self.assertEqual(physical_amount(conn, lid, "wb", 3), 1.01)
