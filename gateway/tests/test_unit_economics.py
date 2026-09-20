import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from datetime import date
from decimal import Decimal

from logic.unit_economics import (
    ALLOWED_ALLOCATION_BASES,
    EXPENSE_ARTICLES,
    CalculatorInput,
    CalculatorValidationError,
    calculate_unit_economics,
)


class UnitEconomicsTests(unittest.TestCase):
    def test_nine_articles_have_source_and_allocation_metadata(self):
        self.assertEqual(len(EXPENSE_ARTICLES), 9)
        self.assertIn("purchase_production", EXPENSE_ARTICLES)
        self.assertIn("taxes", EXPENSE_ARTICLES)
        self.assertEqual(ALLOWED_ALLOCATION_BASES, ("per_unit", "total", "revenue_pct", "price_pct"))

    def test_total_and_percentage_articles_are_allocated_to_unit(self):
        result = calculate_unit_economics(CalculatorInput(
            price_rub=1000,
            units=10,
            articles={
                "purchase_production": {"amount_rub": 4000, "allocation_basis": "total", "source": "manual"},
                "wb_commission": {"amount_rub": 20, "allocation_basis": "price_pct", "source": "wb_api"},
                "taxes": {"amount_rub": 6, "allocation_basis": "revenue_pct", "source": "manual"},
            },
            frozen_capital_rub=5000,
        ))
        self.assertEqual(result.article_per_unit["purchase_production"], Decimal("400"))
        self.assertEqual(result.article_per_unit["wb_commission"], Decimal("200"))
        self.assertEqual(result.article_per_unit["taxes"], Decimal("60"))
        self.assertEqual(result.total_expenses_per_unit, Decimal("660"))
        self.assertEqual(result.profit_per_unit, Decimal("340"))
        self.assertEqual(result.frozen_capital_rub, Decimal("5000"))
        self.assertNotIn("frozen_capital", result.article_per_unit)

    def test_legacy_fields_remain_supported(self):
        result = calculate_unit_economics(CalculatorInput(
            price_rub=1200, units=2, cost_per_unit_rub=400, commission_pct=10,
            logistics_per_unit_rub=90, packaging_per_unit_rub=12, advertising_per_unit_rub=60,
        ))
        self.assertEqual(result.total_expenses_per_unit, Decimal("682"))
        self.assertEqual(result.profit_per_unit, Decimal("518"))

    def test_rejects_invalid_period_source_and_negative_values(self):
        with self.assertRaises(CalculatorValidationError):
            CalculatorInput(price_rub=100, units=1, articles={"taxes": {"amount_rub": -1}})
        with self.assertRaises(CalculatorValidationError):
            CalculatorInput(price_rub=100, units=1, articles={"taxes": {"amount_rub": 1, "source": "unknown"}})
        with self.assertRaises(CalculatorValidationError):
            CalculatorInput(price_rub=100, units=1, articles={"taxes": {"amount_rub": 1, "period_start": "2026-03-10", "period_end": "2026-03-01"}})

    def test_unknown_article_is_rejected_and_zero_units_is_rejected(self):
        with self.assertRaises(CalculatorValidationError):
            CalculatorInput(price_rub=100, units=0)
        with self.assertRaises(CalculatorValidationError):
            CalculatorInput(price_rub=100, units=1, articles={"rent": {"amount_rub": 1}})


if __name__ == "__main__":
    unittest.main()


