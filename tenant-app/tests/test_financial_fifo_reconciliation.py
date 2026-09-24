from __future__ import annotations

import json

import pandas as pd

from tests.base import DbTestCase
from db.core import connect, ensure_financial_schema
from db.financial_fifo_reconciliation import (
    read_financial_fifo_reconciliation,
    summarize_financial_fifo_reconciliation,
)


class FinancialFifoReconciliationTests(DbTestCase):
    NM_ID = 71001

    def setUp(self) -> None:
        super().setUp()
        ensure_financial_schema()

    def _finance(self, rrd_id: int, *, operation_date: str, sale_date: str,
                 quantity: int, srid: str = "SR-1", operation: str = "Продажа",
                 raw_payload: dict | None = None) -> None:
        with connect() as conn:
            conn.execute(
                """INSERT INTO financial_report(
                    rrd_id,operation_date,sale_date,nm_id,supplier_article,quantity,
                    operation_name,doc_type_name,raw_json
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (rrd_id, operation_date, sale_date, self.NM_ID, "QA-ARTICLE", quantity,
                 operation, "Продажа", json.dumps(raw_payload if raw_payload is not None else ({"srid": srid} if srid else {}))),
            )

    def _event(self, record_id: str, *, srid: str = "SR-1", kind: str = "sale",
               units: int = 1, cost: float = 80.0, status: str = "applied",
               source: str = "production", matched_sale_event_id: int | None = None,
               source_raw_payload: dict | None = None) -> int:
        with connect() as conn:
            conn.execute(
                """INSERT INTO sales(id,sale_date,nm_id,supplier_article,sale_id,is_return,raw_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (record_id, "2026-06-02", self.NM_ID, "QA-ARTICLE", record_id,
                 1 if kind == "return" else 0,
                 json.dumps(source_raw_payload if source_raw_payload is not None else ({"srid": srid} if srid else {}))),
            )
            movement_id = None
            if status == "applied":
                movement_id = int(conn.execute(
                    """INSERT INTO inventory_movements(
                        event_key,movement_type,nm_id,quantity,created_at
                    ) VALUES (?,?,?,?,?)""",
                    (f"qa:{record_id}", "qa", self.NM_ID, units, self.now()),
                ).lastrowid)
            event_id = int(conn.execute(
                """INSERT INTO sales_fifo_events(
                    sale_record_id,srid,nm_id,event_date,event_type,units,fifo_cost_rub,
                    unit_cost_rub,status,movement_id,matched_sale_event_id,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (record_id, srid, self.NM_ID, "2026-06-02", kind, units, cost,
                 cost / max(1, units), status, movement_id, matched_sale_event_id,
                 self.now(), self.now()),
            ).lastrowid)
            if movement_id is not None:
                layer_id = int(conn.execute(
                    """INSERT INTO finished_goods_cost_layers(
                        nm_id,source_type,source_ref,original_units,wb_units,unit_cost_rub,
                        original_amount_rub,status,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (self.NM_ID, source, f"qa:{record_id}", units, units,
                     cost / max(1, units), cost, "active", self.now(), self.now()),
                ).lastrowid)
                conn.execute(
                    """INSERT INTO finished_goods_fifo_allocations(
                        movement_id,layer_id,units,amount_rub,from_location,to_location,
                        status,created_at,reversed_at
                    ) VALUES (?,?,?,?,'wb','customer','applied',?,NULL)""",
                    (movement_id, layer_id, units, cost, self.now()),
                )
            return event_id

    def test_multi_quantity_financial_row_sums_distinct_fifo_events(self) -> None:
        self._finance(1, operation_date="2026-06-02", sale_date="2026-06-01", quantity=2)
        self._event("S1", cost=80.0)
        self._event("S2", cost=95.0)

        result = read_financial_fifo_reconciliation("2026-06-02", "2026-06-02")

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertEqual(row["status"], "exact")
        self.assertEqual(row["matched_units"], 2)
        self.assertAlmostEqual(row["eligible_fifo_cogs_rub"], 175.0)

    def test_return_uses_negative_cost_in_financial_operation_period(self) -> None:
        sale_event_id = self._event("S1", cost=80.0)
        self._event("R1", kind="return", cost=80.0, matched_sale_event_id=sale_event_id)
        self._finance(1, operation_date="2026-06-02", sale_date="2026-05-01",
                      quantity=1, operation="Возврат")

        result = read_financial_fifo_reconciliation("2026-06-01", "2026-06-30")

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertEqual(row["event_type"], "return")
        self.assertEqual(row["operation_date"], "2026-06-02")
        self.assertAlmostEqual(row["eligible_fifo_cogs_rub"], -80.0)

    def test_duplicate_financial_srid_rows_are_not_assigned_cost(self) -> None:
        self._finance(1, operation_date="2026-06-02", sale_date="2026-06-01", quantity=1)
        self._finance(2, operation_date="2026-06-02", sale_date="2026-06-01", quantity=1)
        self._event("S1", cost=80.0)

        result = read_financial_fifo_reconciliation("2026-06-02", "2026-06-02")

        self.assertEqual(set(result["status"]), {"ambiguous"})
        self.assertTrue(result["eligible_fifo_cogs_rub"].isna().all())
        self.assertTrue((result["reason"] == "duplicate_financial_rows_for_srid").all())

    def test_missing_srid_and_unit_mismatch_do_not_claim_exact_cost(self) -> None:
        self._finance(1, operation_date="2026-06-02", sale_date="2026-06-01", quantity=1, srid="")
        self._finance(2, operation_date="2026-06-02", sale_date="2026-06-01", quantity=2, srid="SR-2")
        self._event("S2", srid="SR-2", cost=80.0)

        result = read_financial_fifo_reconciliation("2026-06-02", "2026-06-02").set_index("rrd_id")

        self.assertEqual(result.loc[1, "reason"], "financial_srid_missing")
        self.assertEqual(result.loc[2, "reason"], "fifo_financial_unit_count_mismatch")
        self.assertTrue(result["eligible_fifo_cogs_rub"].isna().all())

    def test_odid_alone_is_not_used_as_srid(self) -> None:
        self._finance(1, operation_date="2026-06-02", sale_date="2026-06-01",
                      quantity=1, srid="", raw_payload={"odid": "SR-1"})
        self._event("S1", srid="SR-1", cost=80.0)

        result = read_financial_fifo_reconciliation("2026-06-02", "2026-06-02")

        self.assertEqual(result.iloc[0]["status"], "unmatched")
        self.assertEqual(result.iloc[0]["reason"], "financial_srid_missing")
        self.assertTrue(pd.isna(result.iloc[0]["eligible_fifo_cogs_rub"]))

    def test_sales_source_odid_does_not_validate_event_srid(self) -> None:
        self._finance(1, operation_date="2026-06-02", sale_date="2026-06-01", quantity=1)
        self._event("S1", srid="SR-1", cost=80.0, source_raw_payload={"odid": "SR-1"})

        result = read_financial_fifo_reconciliation("2026-06-02", "2026-06-02")

        self.assertEqual(result.iloc[0]["status"], "unmatched")
        self.assertEqual(result.iloc[0]["reason"], "source_sales_srid_missing")
        self.assertTrue(pd.isna(result.iloc[0]["eligible_fifo_cogs_rub"]))

    def test_sales_source_srid_must_equal_financial_srid(self) -> None:
        self._finance(1, operation_date="2026-06-02", sale_date="2026-06-01", quantity=1)
        self._event("S1", srid="SR-1", cost=80.0, source_raw_payload={"srid": "OTHER"})

        result = read_financial_fifo_reconciliation("2026-06-02", "2026-06-02")

        self.assertEqual(result.iloc[0]["status"], "ambiguous")
        self.assertEqual(result.iloc[0]["reason"], "source_sales_srid_mismatch")
        self.assertTrue(pd.isna(result.iloc[0]["eligible_fifo_cogs_rub"]))

    def test_baseline_and_placeholder_layers_are_not_exact(self) -> None:
        self._finance(1, operation_date="2026-06-02", sale_date="2026-06-01", quantity=1)
        self._finance(2, operation_date="2026-06-02", sale_date="2026-06-01", quantity=1, srid="SR-2")
        self._event("S1", cost=0.0, status="baseline")
        self._event("S2", srid="SR-2", cost=80.0, source="synthetic_sale")
        with connect() as conn:
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES (?,?)", (self.NM_ID, 31.0))

        result = read_financial_fifo_reconciliation("2026-06-02", "2026-06-02").set_index("rrd_id")

        self.assertEqual(result.loc[1, "status"], "baseline")
        self.assertAlmostEqual(result.loc[1, "baseline_cogs_rub"], 31.0)
        self.assertEqual(result.loc[2, "status"], "synthetic")
        self.assertIn("placeholder_or_opening_layer", result.loc[2, "reason"])
        self.assertTrue(result["eligible_fifo_cogs_rub"].isna().all())

    def test_summary_counts_only_sales_and_returns_and_never_overstates_exact_cost(self) -> None:
        self._finance(1, operation_date="2026-06-02", sale_date="2026-06-01", quantity=1)
        self._finance(2, operation_date="2026-06-02", sale_date="2026-06-01", quantity=1, srid="SR-2")
        self._event("S1", cost=80.0)
        self._event("S2", srid="SR-2", cost=60.0, source="synthetic_sale")
        with connect() as conn:
            conn.execute(
                "INSERT INTO financial_report(rrd_id,operation_date,quantity,operation_name,raw_json) VALUES (3,'2026-06-02',1,'Хранение','{}')"
            )

        rows = read_financial_fifo_reconciliation("2026-06-02", "2026-06-02")
        summary = summarize_financial_fifo_reconciliation(rows)

        self.assertEqual(summary["sale_return_rows"], 2)
        self.assertEqual(summary["exact_rows"], 1)
        self.assertEqual(summary["status_counts"], {"exact": 1, "synthetic": 1})
        self.assertAlmostEqual(summary["exact_fifo_cogs_rub"], 80.0)

    def test_empty_summary_is_zeroed(self) -> None:
        summary = summarize_financial_fifo_reconciliation(read_financial_fifo_reconciliation("2026-01-01", "2026-01-31"))
        self.assertEqual(summary, {
            "sale_return_rows": 0,
            "exact_rows": 0,
            "exact_fifo_cogs_rub": 0.0,
            "status_counts": {},
        })


if __name__ == "__main__":
    import unittest
    unittest.main()
