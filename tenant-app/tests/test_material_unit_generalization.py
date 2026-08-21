"""Unit tests for the raw-material unit-of-measure generalization.

Covers the "large rework" that lets a tenant track a raw material either
"packaged" (rolls/boxes of a fixed size, the original behaviour) or as a
plain running "quantity" (kilograms, litres, pieces -- anything without a
fixed package size). See ui_helpers.py's NO_PACKAGE_ROLL_LENGTH /
is_packaged_material / packages_to_buy for the design rationale.
"""
from __future__ import annotations

import pandas as pd

from db import fifo_materials as fm
from db import procurement
from db import production
from db.core import connect

from .base import DbTestCase


class SaveMaterialInventoryQuantityModeTests(DbTestCase):
    def test_quantity_mode_pins_full_rolls_zero_and_sentinel_roll_length(self) -> None:
        df = pd.DataFrame([{
            "material_name": "Клей ПВА",
            "balance_known": True,
            "full_rolls": 5,          # should be ignored/overridden for quantity mode
            "partial_meters": 12.5,   # the whole stock, in the material's own unit
            "roll_length": 25.5,      # should be overridden to the sentinel
            "unit": "кг",
            "tracking_mode": "quantity",
            "opening_rate_rub": 0.0,
            "note": "",
        }])
        production.save_material_inventory(df)
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM material_inventory_color WHERE material_key=?", ("клей пва",)
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["full_rolls"], 0)
        self.assertEqual(row["roll_length"], production.NO_PACKAGE_ROLL_LENGTH)
        self.assertEqual(row["partial_meters"], 12.5)
        self.assertEqual(row["unit"], "кг")
        self.assertEqual(row["tracking_mode"], "quantity")

    def test_packaged_mode_keeps_real_roll_length_and_full_rolls(self) -> None:
        df = pd.DataFrame([{
            "material_name": "Красный ПВХ",
            "balance_known": True,
            "full_rolls": 3,
            "partial_meters": 4.2,
            "roll_length": 25.5,
            "unit": "м",
            "tracking_mode": "packaged",
            "opening_rate_rub": 0.0,
            "note": "",
        }])
        production.save_material_inventory(df)
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM material_inventory_color WHERE material_key=?", ("красный пвх",)
            ).fetchone()
        self.assertEqual(row["full_rolls"], 3)
        self.assertEqual(row["roll_length"], 25.5)
        self.assertEqual(row["tracking_mode"], "packaged")

    def test_backward_compatible_without_new_columns(self) -> None:
        """A caller that never supplies unit/tracking_mode/opening_rate_rub (the
        pre-rework shape) must still work and default to today's behaviour."""
        df = pd.DataFrame([{
            "material_name": "Синий ПВХ",
            "balance_known": True,
            "full_rolls": 2,
            "partial_meters": 1.0,
            "roll_length": 25.5,
            "note": "",
        }])
        production.save_material_inventory(df)
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM material_inventory_color WHERE material_key=?", ("синий пвх",)
            ).fetchone()
        self.assertEqual(row["unit"], "м")
        self.assertEqual(row["tracking_mode"], "packaged")
        self.assertEqual(row["opening_rate_rub"], 0.0)


class ApplyMaterialDeltaQuantityModeTests(DbTestCase):
    def test_quantity_mode_consumption_and_shortage_message_uses_own_unit(self) -> None:
        df = pd.DataFrame([{
            "material_name": "Смазка",
            "balance_known": True,
            "full_rolls": 0,
            "partial_meters": 10.0,
            "roll_length": 25.5,
            "unit": "л",
            "tracking_mode": "quantity",
            "opening_rate_rub": 0.0,
            "note": "",
        }])
        production.save_material_inventory(df)
        with connect() as conn:
            previous_total, new_total = production._apply_material_delta(conn, "смазка", "Смазка", -4.0)
        self.assertAlmostEqual(previous_total, 10.0, places=3)
        self.assertAlmostEqual(new_total, 6.0, places=3)
        with connect() as conn:
            with self.assertRaises(ValueError) as ctx:
                production._apply_material_delta(conn, "смазка", "Смазка", -100.0)
        self.assertIn("л", str(ctx.exception))


class InitializeMaterialFifoPerMaterialRateTests(DbTestCase):
    def test_per_material_rate_overrides_tenant_wide_rate(self) -> None:
        df = pd.DataFrame([
            {
                "material_name": "Материал с своей ставкой",
                "balance_known": True, "full_rolls": 2, "partial_meters": 0.0,
                "roll_length": 10.0, "unit": "м", "tracking_mode": "packaged",
                "opening_rate_rub": 777.0, "note": "",
            },
            {
                "material_name": "Материал без своей ставки",
                "balance_known": True, "full_rolls": 1, "partial_meters": 0.0,
                "roll_length": 10.0, "unit": "м", "tracking_mode": "packaged",
                "opening_rate_rub": 0.0, "note": "",
            },
        ])
        production.save_material_inventory(df)
        result = fm.initialize_material_fifo(50.0)  # tenant-wide fallback rate
        self.assertEqual(result["created"], 2)
        with connect() as conn:
            own_rate_layer = conn.execute(
                "SELECT * FROM material_cost_layers WHERE material_key=?",
                ("материал с своей ставкой",),
            ).fetchone()
            fallback_layer = conn.execute(
                "SELECT * FROM material_cost_layers WHERE material_key=?",
                ("материал без своей ставки",),
            ).fetchone()
        self.assertEqual(own_rate_layer["unit_cost_rub_m"], 777.0)
        self.assertEqual(fallback_layer["unit_cost_rub_m"], 50.0)


class MaterialRateMapsUnitAwareTests(DbTestCase):
    """Regression guard: found while generalizing rolls-only material tracking --
    the price-per-base-unit calculations used to unconditionally divide the
    purchased price by roll_length, which is correct only for unit=="рулон".
    For any other unit (a plain quantity, e.g. kilograms) that silently
    produced a near-zero or wildly wrong rate."""

    def test_roll_purchase_rate_divides_by_roll_length(self) -> None:
        procurement.create_procurement_order(
            procurement_type="Сырьё", supplier_name="Поставщик рулонов",
            status="Заказано", order_date="2026-01-01", payment_due_date=None,
            expected_date="2026-01-10", note="",
            items=pd.DataFrame([{
                "material_name": "Материал в рулонах",
                "unit": "рулон", "roll_length": 25.0, "quantity": 2,
                "supplier_unit_price": 500.0, "exchange_rate": 1.0,
            }]),
        )
        with connect() as conn:
            exact, fallback, _src, _fsrc = fm._material_rate_maps(conn)
        self.assertAlmostEqual(exact["материал в рулонах"], 500.0 / 25.0, places=4)

    def test_quantity_purchase_rate_is_used_as_is_not_divided(self) -> None:
        procurement.create_procurement_order(
            procurement_type="Сырьё", supplier_name="Поставщик кг",
            status="Заказано", order_date="2026-01-01", payment_due_date=None,
            expected_date="2026-01-10", note="",
            items=pd.DataFrame([{
                "material_name": "Материал по весу",
                "unit": "кг", "roll_length": production.NO_PACKAGE_ROLL_LENGTH,
                "quantity": 10,
                "supplier_unit_price": 300.0, "exchange_rate": 1.0,
            }]),
        )
        with connect() as conn:
            exact, fallback, _src, _fsrc = fm._material_rate_maps(conn)
        # Must be the raw price (300 rub/kg), NOT price / NO_PACKAGE_ROLL_LENGTH (~0).
        self.assertAlmostEqual(exact["материал по весу"], 300.0, places=4)

    def test_read_material_cost_rates_does_not_collapse_quantity_mode_rate(self) -> None:
        procurement.create_procurement_order(
            procurement_type="Сырьё", supplier_name="Поставщик л",
            status="Заказано", order_date="2026-01-01", payment_due_date=None,
            expected_date="2026-01-10", note="",
            items=pd.DataFrame([{
                "material_name": "Материал в литрах",
                "unit": "л", "roll_length": 1_000_000_000.0, "quantity": 5,
                "supplier_unit_price": 120.0, "exchange_rate": 1.0,
            }]),
        )
        frame = fm.read_material_cost_rates()
        row = frame[frame["material_key"] == "материал в литрах"].iloc[0]
        self.assertAlmostEqual(float(row["cost_per_meter_rub"]), 120.0, places=4)


class PackagesToBuyHelperTests(DbTestCase):
    """These exercise ui_helpers' pure helpers directly -- imported lazily here
    because ui_helpers.py imports streamlit at module level, which isn't
    installed in every environment this suite runs in."""

    def _helpers(self):
        try:
            import ui_helpers as uh
        except ImportError:
            self.skipTest("streamlit not installed; ui_helpers cannot be imported here")
        return uh

    def test_is_packaged_material_true_for_real_roll_length(self) -> None:
        uh = self._helpers()
        self.assertTrue(uh.is_packaged_material(25.5))
        self.assertTrue(uh.is_packaged_material(1.0))

    def test_is_packaged_material_false_for_sentinel(self) -> None:
        uh = self._helpers()
        self.assertFalse(uh.is_packaged_material(uh.NO_PACKAGE_ROLL_LENGTH))

    def test_packages_to_buy_zero_for_quantity_mode_even_with_shortage(self) -> None:
        """Regression guard: ceil(tiny_shortage / SENTINEL) must not return 1."""
        uh = self._helpers()
        self.assertEqual(uh.packages_to_buy(0.001, uh.NO_PACKAGE_ROLL_LENGTH, True), 0)
        self.assertEqual(uh.packages_to_buy(500.0, uh.NO_PACKAGE_ROLL_LENGTH, True), 0)

    def test_packages_to_buy_zero_when_balance_unknown(self) -> None:
        uh = self._helpers()
        self.assertEqual(uh.packages_to_buy(100.0, 25.5, False), 0)

    def test_packages_to_buy_rounds_up_for_packaged_material(self) -> None:
        uh = self._helpers()
        self.assertEqual(uh.packages_to_buy(0.1, 25.5, True), 1)
        self.assertEqual(uh.packages_to_buy(51.0, 25.5, True), 2)   # exactly 2 packages, no rounding needed
        self.assertEqual(uh.packages_to_buy(51.1, 25.5, True), 3)   # a hair over 2 packages rounds up to 3
        self.assertEqual(uh.packages_to_buy(0.0, 25.5, True), 0)
