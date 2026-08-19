"""Unit tests for db/wb_fifo_reconstruction_v67.py -- the retroactive FIFO
reconstruction engine.

This module is the largest and riskiest piece of the money-logic layer (it
rewrites historical FIFO costs after the fact, backed by a full SQLite
file backup/restore). Two things are deliberately IN SCOPE here, both
testable without touching the filesystem:

1. _resolve_reconstruction_cost_basis_with_conn -- the pure, read-only
   priority cascade that decides which historical rate to trust for a
   reconstructed layer. Getting this priority order wrong would silently
   understate/overstate historical COGS.
2. The safety guards at the very top of apply_retroactive_fifo_reconstruction_v67
   and rollback_retroactive_fifo_reconstruction_v67 -- the exact-text
   confirmation and the readiness/status gates. These run and raise BEFORE
   any file is touched, so they're safe to exercise directly.

Deliberately OUT OF SCOPE: the actual backup -> BEGIN IMMEDIATE -> replay
-> commit happy path of apply/rollback. That path does a raw
`sqlite3.connect(str(Path(DB_PATH)...))` using this module's own
module-level `DB_PATH` (imported once from config at import time), which
is a *different* name binding than `db.core.DB_PATH` -- the tests/base.py
DbTestCase pattern that monkeypatches `db.core.DB_PATH` for every other
test module in this suite does NOT reach it. Exercising the real
backup/restore path would need a real on-disk database file and patching
three separate DB_PATH bindings (config, core, this module) in sync, on
top of building a full synthetic WB supply/sale history large enough for
read_fifo_reconstruction_v67_readiness() to report ready=True. That's a
real-host integration test, not a unit test -- see DEPLOY.md.
"""
from __future__ import annotations

from db.core import connect
from db.wb_fifo_reconstruction_v67 import (
    _resolve_reconstruction_cost_basis_with_conn,
    apply_retroactive_fifo_reconstruction_v67,
    read_fifo_reconstruction_v67_readiness,
    rollback_retroactive_fifo_reconstruction_v67,
)

from .base import DbTestCase

NM_ID = 950001


class ResolveReconstructionCostBasisTests(DbTestCase):
    def test_missing_nm_id_returns_missing_source(self) -> None:
        with connect() as conn:
            result = _resolve_reconstruction_cost_basis_with_conn(conn, 0, "2026-01-15")
        self.assertEqual(result["source_code"], "missing")
        self.assertEqual(result["rate"], 0.0)

    def test_no_evidence_at_all_returns_missing_source(self) -> None:
        with connect() as conn:
            result = _resolve_reconstruction_cost_basis_with_conn(conn, NM_ID, "2026-01-15")
        self.assertEqual(result["source_code"], "missing")

    def test_prefers_actual_production_batch_over_everything_else(self) -> None:
        with connect() as conn:
            # Lower-priority evidence also present -- must still lose to the production batch.
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES (?,999.0)", (NM_ID,))
            conn.execute(
                """
                INSERT INTO production_cost_batches(
                    movement_id,nm_id,batch_date,produced_units,total_cost_rub,unit_cost_rub,status,created_at
                ) VALUES (1,?,?,10,1000.0,100.0,'active',?)
                """,
                (NM_ID, "2026-01-10", self.now()),
            )
        with connect() as conn:
            result = _resolve_reconstruction_cost_basis_with_conn(conn, NM_ID, "2026-01-15")
        self.assertEqual(result["source_code"], "actual_production")
        self.assertAlmostEqual(result["rate"], 100.0, places=2)

    def test_production_batch_after_as_of_date_is_ignored(self) -> None:
        with connect() as conn:
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES (?,50.0)", (NM_ID,))
            conn.execute(
                """
                INSERT INTO production_cost_batches(
                    movement_id,nm_id,batch_date,produced_units,total_cost_rub,unit_cost_rub,status,created_at
                ) VALUES (1,?,?,10,1000.0,100.0,'active',?)
                """,
                (NM_ID, "2026-02-01", self.now()),  # AFTER as_of
            )
        with connect() as conn:
            result = _resolve_reconstruction_cost_basis_with_conn(conn, NM_ID, "2026-01-15")
        # Falls through to the baseline cost since the batch is in the future relative to as_of.
        self.assertEqual(result["source_code"], "baseline_cost")
        self.assertAlmostEqual(result["rate"], 50.0, places=2)

    def test_falls_back_to_historical_fifo_when_no_production_batch(self) -> None:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO finished_goods_cost_layers(
                    nm_id,supplier_article,product_name,source_type,source_ref,source_date,
                    original_units,ready_units,inbound_units,wb_units,unit_cost_rub,original_amount_rub,
                    status,note,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'depleted','',?,?)
                """,
                (NM_ID, "ART-1", "Тест", "opening", "seed:1", "2026-01-05", 4, 0, 0, 0, 70.0, 280.0, self.now(), self.now()),
            )
        with connect() as conn:
            result = _resolve_reconstruction_cost_basis_with_conn(conn, NM_ID, "2026-01-15")
        self.assertEqual(result["source_code"], "historical_fifo")
        self.assertAlmostEqual(result["rate"], 70.0, places=2)

    def test_synthetic_layers_are_excluded_from_historical_fifo_evidence(self) -> None:
        """synthetic_sale / return_unmatched / opening_wb layers are placeholders,
        not real historical cost evidence -- they must never be promoted as if
        they were an ordinary FIFO layer rate."""
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO finished_goods_cost_layers(
                    nm_id,supplier_article,product_name,source_type,source_ref,source_date,
                    original_units,ready_units,inbound_units,wb_units,unit_cost_rub,original_amount_rub,
                    status,note,created_at,updated_at
                ) VALUES (?,?,?,'synthetic_sale',?,?,?,?,?,?,?,?, 'depleted','',?,?)
                """,
                (NM_ID, "ART-1", "Тест", "seed:synthetic", "2026-01-05", 1, 0, 0, 0, 999.0, 999.0, self.now(), self.now()),
            )
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES (?,30.0)", (NM_ID,))
        with connect() as conn:
            result = _resolve_reconstruction_cost_basis_with_conn(conn, NM_ID, "2026-01-15")
        self.assertEqual(result["source_code"], "baseline_cost")
        self.assertAlmostEqual(result["rate"], 30.0, places=2)

    def test_falls_back_to_baseline_cost_when_no_fifo_evidence(self) -> None:
        with connect() as conn:
            conn.execute("INSERT INTO costs(nm_id,cost_per_wb_unit) VALUES (?,42.0)", (NM_ID,))
        with connect() as conn:
            result = _resolve_reconstruction_cost_basis_with_conn(conn, NM_ID, "2026-01-15")
        self.assertEqual(result["source_code"], "baseline_cost")
        self.assertAlmostEqual(result["rate"], 42.0, places=2)

    def test_falls_back_to_forecast_cost_when_baseline_cost_is_zero(self) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT INTO costs(nm_id,cost_per_wb_unit,forecast_total_cost_rub) VALUES (?,0,66.0)", (NM_ID,)
            )
        with connect() as conn:
            result = _resolve_reconstruction_cost_basis_with_conn(conn, NM_ID, "2026-01-15")
        self.assertEqual(result["source_code"], "forecast_cost")
        self.assertAlmostEqual(result["rate"], 66.0, places=2)

    def test_falls_back_to_current_weighted_fifo_as_last_resort(self) -> None:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO finished_goods_cost_layers(
                    nm_id,supplier_article,product_name,source_type,source_ref,source_date,
                    original_units,ready_units,inbound_units,wb_units,unit_cost_rub,original_amount_rub,
                    status,note,created_at,updated_at
                ) VALUES (?,?,?,'opening',?,?,?,?,?,?,?,?, 'active','',?,?)
                """,
                (NM_ID, "ART-1", "Тест", "seed:current", "2026-03-01", 6, 0, 0, 6, 90.0, 540.0, self.now(), self.now()),
            )
        # as_of predates the only evidence (which is also dated AFTER as_of, so the
        # date-bounded historical/opening lookups can't use it either) -- only the
        # unconditional "current weighted FIFO" fallback can still find it.
        with connect() as conn:
            result = _resolve_reconstruction_cost_basis_with_conn(conn, NM_ID, "2026-01-15")
        self.assertEqual(result["source_code"], "current_fifo_weighted")
        self.assertAlmostEqual(result["rate"], 90.0, places=2)


class ReadinessGateTests(DbTestCase):
    def test_empty_database_is_not_ready(self) -> None:
        summary, suspense, blockers = read_fifo_reconstruction_v67_readiness()
        self.assertFalse(summary["ready"])
        self.assertTrue(summary["reason"])  # a human-readable reason is always present
        self.assertEqual(summary["candidate_loss_units"], 0)


class ApplyGuardTests(DbTestCase):
    def test_wrong_confirm_text_is_rejected_before_touching_anything(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            apply_retroactive_fifo_reconstruction_v67("yes, do it")
        self.assertIn("APPLY", str(ctx.exception))
        with connect() as conn:
            runs = conn.execute("SELECT COUNT(*) AS n FROM fifo_reconstruction_apply_runs").fetchone()["n"]
        self.assertEqual(runs, 0)  # confirm-text guard fired before any run row was created

    def test_correct_confirm_text_still_blocked_when_not_ready(self) -> None:
        # On an empty db candidate_loss_units is 0, so the expected phrase is "APPLY 0".
        with self.assertRaises(ValueError) as ctx:
            apply_retroactive_fifo_reconstruction_v67("APPLY 0")
        self.assertNotIn("APPLY", str(ctx.exception))  # past the confirm-text guard now
        with connect() as conn:
            runs = conn.execute("SELECT COUNT(*) AS n FROM fifo_reconstruction_apply_runs").fetchone()["n"]
        self.assertEqual(runs, 0)  # readiness guard also fired before any run row was created

    def test_confirm_text_comparison_is_case_and_whitespace_insensitive(self) -> None:
        # Still blocked by the readiness gate on an empty db, but must get PAST the
        # confirm-text check first (proven by the error message no longer mentioning APPLY).
        with self.assertRaises(ValueError) as ctx:
            apply_retroactive_fifo_reconstruction_v67("  apply 0  ")
        self.assertNotIn("APPLY", str(ctx.exception))


class RollbackGuardTests(DbTestCase):
    def test_wrong_confirm_text_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            rollback_retroactive_fifo_reconstruction_v67(1, "nope")
        self.assertIn("ROLLBACK", str(ctx.exception))

    def test_unknown_run_id_is_rejected_after_confirm_text_passes(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            rollback_retroactive_fifo_reconstruction_v67(999, "ROLLBACK 999")
        self.assertIn("applied", str(ctx.exception))

    def test_rollback_of_non_applied_run_is_rejected(self) -> None:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO fifo_reconstruction_apply_runs(
                    run_key,version,status,created_at
                ) VALUES ('run:1','6.7','rolled_back',?)
                """,
                (self.now(),),
            )
            run_id = conn.execute("SELECT id FROM fifo_reconstruction_apply_runs WHERE run_key='run:1'").fetchone()["id"]
        with self.assertRaises(ValueError):
            rollback_retroactive_fifo_reconstruction_v67(int(run_id), f"ROLLBACK {run_id}")


if __name__ == "__main__":
    import unittest
    unittest.main()
