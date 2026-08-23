"""Happy-path coverage for the v6.7 Apply/rollback backup -> BEGIN IMMEDIATE ->
replay -> commit flow, deliberately left OUT OF SCOPE by
tests/test_wb_fifo_reconstruction_v67.py (see that file's module docstring).

Why this needs its own base class instead of reusing tests.base.DbTestCase as-is
-----------------------------------------------------------------------------
db/wb_fifo_reconstruction_v67.py does `from config import DB_PATH` at import
time (top of the module). That creates a name binding in the
`db.wb_fifo_reconstruction_v67` module namespace that is *independent* of
`db.core.DB_PATH` -- rebinding one does not rebind the other, they just
happen to start out pointing at the same object. tests/base.py's
DbTestCase.setUp() only monkeypatches `db.core.DB_PATH` (which is enough for
every other test module, because they all go through db.core.connect()).
That is NOT enough here: `_v67_create_sqlite_backup` and the apply/rollback
functions do a raw, file-level `sqlite3.connect(str(Path(DB_PATH)...))` using
*this module's own* `DB_PATH` name to make a real on-disk copy of the sqlite
file before mutating it. If we only patched db.core.DB_PATH, the backup/
restore code would silently keep reading/writing the real
data/wb_dashboard.sqlite3 on disk while the rest of the test read/wrote the
throwaway temp db -- exactly the kind of bug this suite exists to prevent.

So this test case patches THREE separate DB_PATH bindings to the same real
temp-file path (not ':memory:', since the backup step does an actual file
copy via sqlite3.Connection.backup()):
  1. config.DB_PATH             -- the original source of truth
  2. db.core.DB_PATH            -- what db.core.connect()/init_db() use
  3. wb_fifo_reconstruction_v67.DB_PATH -- this module's own private copy

Why the readiness gate needs a small but specific synthetic dataset
-----------------------------------------------------------------------------
read_fifo_reconstruction_v67_readiness() is a strict AND of several
independently-computed signals (see db/wb_fifo_reconstruction_v67.py around
line 1184). Tracing preview_retroactive_fbw_fifo_reconstruction() and
read_wb_final_evidence_audit() (and the incident-reconciliation helpers in
db/wb_fifo_reconciliation.py they call) down to their SQL shows the minimal
combination that reaches ready=True with candidate_loss_units > 0, with NO
sales/returns replay needed at all:

- Two `stocks` snapshots for the same nm_id: an earlier "detailed" one
  (warehouse_id != -999999) at a warehouse matching one of the hard-coded
  WB_INCIDENT_WAREHOUSE_PATTERNS (e.g. "Коледино") with quantity > 0, and a
  later "current" one where that nm_id's contour has dropped to 0. This is
  exactly what produces a positive external_gap_units/incident_exposure_units
  overlap -- i.e. a nonzero incident candidate-loss signal -- while also
  making preview_layer_shortfall_units trivially 0 (contour is already 0, so
  the preview can never fall short of it). That keeps `residual_units == 0`
  for free, with no sale/return events required.
- One accepted, status_id=5 `wb_fbw_supply_confirmations` (+ matching
  `wb_fbw_supply_goods`) row dated after the baseline snapshot: without at
  least one such verified receipt, preview_retroactive_fbw_fifo_reconstruction
  early-returns ready=False ("После базового снимка нет подтверждённых
  принятых FBW-поставок").
- A `costs.cost_per_wb_unit` row for the nm_id: this is what lets
  _resolve_reconstruction_cost_basis_with_conn find a nonzero rate (source
  "baseline_cost") for both the synthetic contour-bridge layer and the
  verified-supply layer. Without it every reconstructed unit would have
  rate <= 0, which read_wb_final_evidence_audit reports as a missing-cost-
  basis blocker and readiness would stay False forever.

With that in place, Apply only has to materialize two brand-new
finished_goods_cost_layers rows (a "reconstruction_bridge" layer for the
pre-existing physical contour, and a "verified_fbw_supply" layer for the
verified receipt) and no FIFO event replay at all -- which is precisely the
part of the backup -> BEGIN IMMEDIATE -> replay -> commit path that the
other test module could not exercise.
"""
from __future__ import annotations

import config
import db.core as core
import db.wb_fifo_reconstruction_v67 as v67
from db.core import connect
from db.wb_fifo_reconstruction_v67 import (
    apply_retroactive_fifo_reconstruction_v67,
    read_fifo_reconstruction_v67_readiness,
    rollback_retroactive_fifo_reconstruction_v67,
)

from .base import DbTestCase

NM_ID = 950070
BASELINE_AT = "2026-01-01T00:00:00"
SUPPLY_AT = "2026-01-05T00:00:00"
CURRENT_AT = "2026-01-10T00:00:00"


class ApplyRollbackHappyPathTests(DbTestCase):
    """Exercises the real file-backup -> BEGIN IMMEDIATE -> replay -> commit path."""

    def setUp(self) -> None:
        super().setUp()
        # See module docstring: db.core.DB_PATH alone (patched by the base class)
        # is not enough -- this module's own DB_PATH binding, and config's, must
        # point at the exact same on-disk file for the real backup/restore calls
        # to touch the throwaway test database instead of the real one.
        self._original_config_db_path = config.DB_PATH
        self._original_v67_db_path = v67.DB_PATH
        config.DB_PATH = self.db_path
        v67.DB_PATH = self.db_path

    def tearDown(self) -> None:
        config.DB_PATH = self._original_config_db_path
        v67.DB_PATH = self._original_v67_db_path
        super().tearDown()

    def _seed_ready_dataset(self) -> None:
        """Minimal synthetic evidence that pushes read_fifo_reconstruction_v67_readiness()
        to ready=True with a nonzero candidate_loss_units (see module docstring for why
        each row is here)."""
        with connect() as conn:
            # Baseline ("detailed") snapshot: 10 units sitting at an incident-pattern
            # warehouse. This is the physical contour the reconstruction bridge must cover.
            conn.execute(
                """
                INSERT INTO stocks(
                    snapshot_at,nm_id,chrt_id,warehouse_id,warehouse_name,region_name,
                    quantity,in_way_to_client,in_way_from_client,raw_json
                ) VALUES (?,?,0,100,'Коледино','Test',?,0,0,'{}')
                """,
                (BASELINE_AT, NM_ID, 10),
            )
            # Current (latest) snapshot: the same SKU's contour has fallen to 0. This is
            # what creates the external_gap/candidate-loss signal, and also guarantees the
            # preview can never report a post-replay shortfall for this SKU (0 - anything
            # non-negative is never > 0), so residual_units stays 0 with no replay needed.
            conn.execute(
                """
                INSERT INTO stocks(
                    snapshot_at,nm_id,chrt_id,warehouse_id,warehouse_name,region_name,
                    quantity,in_way_to_client,in_way_from_client,raw_json
                ) VALUES (?,?,0,100,'Коледино','Test',?,0,0,'{}')
                """,
                (CURRENT_AT, NM_ID, 0),
            )
            # Baseline cost rate: the only cost-basis evidence needed so that both the
            # contour-bridge layer and the verified-supply layer get a rate > 0 (source
            # "baseline_cost" in _resolve_reconstruction_cost_basis_with_conn's cascade).
            conn.execute(
                "INSERT INTO costs(nm_id,supplier_article,cost_per_wb_unit,updated_at) VALUES (?,?,?,?)",
                (NM_ID, "ART-1", 100.0, self.now()),
            )
            # One verified, accepted FBW supply after the baseline snapshot -- required or
            # preview_retroactive_fbw_fifo_reconstruction() early-returns ready=False.
            conn.execute(
                """
                INSERT INTO wb_fbw_supply_confirmations(
                    supply_id,fact_date,fact_date_msk,create_date,status_id,warehouse_name,
                    accepted,sku_count,units,raw_json,verified_at
                ) VALUES (5001,?,?,?,5,'Коледино',1,1,3,'{}',?)
                """,
                (SUPPLY_AT, SUPPLY_AT, BASELINE_AT, self.now()),
            )
            conn.execute(
                """
                INSERT INTO wb_fbw_supply_goods(supply_id,nm_id,supplier_article,quantity,raw_json,verified_at)
                VALUES (5001,?,?,3,'{}',?)
                """,
                (NM_ID, "ART-1", self.now()),
            )

    def test_apply_then_rollback_happy_path(self) -> None:
        self._seed_ready_dataset()

        # 1) Readiness gate must report ready=True with the exact candidate figure the
        # confirm-text is derived from.
        readiness, suspense, blockers = read_fifo_reconstruction_v67_readiness()
        self.assertTrue(readiness["ready"], readiness.get("reason"))
        self.assertEqual(readiness["candidate_loss_units"], 10)
        self.assertEqual(readiness["bridge_units"], 10)
        self.assertEqual(readiness["verified_supply_units"], 3)
        self.assertEqual(readiness["expected_fifo_units"], 13)
        self.assertTrue(suspense.empty)
        self.assertTrue(blockers.empty)

        with connect() as conn:
            layers_before = conn.execute(
                "SELECT COUNT(*) AS n FROM finished_goods_cost_layers"
            ).fetchone()["n"]
        self.assertEqual(layers_before, 0)

        # 2) Apply: real backup -> BEGIN IMMEDIATE -> replay -> commit.
        confirm_text = f"APPLY {readiness['candidate_loss_units']}"
        result = apply_retroactive_fifo_reconstruction_v67(confirm_text)

        self.assertTrue(result["ok"])
        run_id = int(result["run_id"])
        self.assertGreater(run_id, 0)
        self.assertEqual(result["fifo_units"], 13)
        self.assertEqual(result["bridge_units"], 10)
        self.assertEqual(result["verified_supply_units"], 3)
        self.assertEqual(result["new_layers"], 2)
        self.assertEqual(result["new_original_units"], 13)
        backup_path = result["backup_path"]
        self.assertTrue(backup_path)
        from pathlib import Path
        self.assertTrue(Path(backup_path).exists(), "apply must leave a real on-disk backup file")

        # The run row and the two new FIFO layers are concrete, checkable side effects --
        # not just "no exception was raised".
        with connect() as conn:
            run_row = conn.execute(
                "SELECT status,applied_fifo_units,candidate_loss_units FROM fifo_reconstruction_apply_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            layers = conn.execute(
                "SELECT source_type,original_units,wb_units,unit_cost_rub,status FROM finished_goods_cost_layers "
                "WHERE nm_id=? ORDER BY id",
                (NM_ID,),
            ).fetchall()
        self.assertEqual(run_row["status"], "applied")
        self.assertEqual(run_row["applied_fifo_units"], 13)
        self.assertEqual(run_row["candidate_loss_units"], 10)
        self.assertEqual(len(layers), 2)
        layers_by_type = {row["source_type"]: row for row in layers}
        self.assertIn("reconstruction_bridge", layers_by_type)
        self.assertIn("verified_fbw_supply", layers_by_type)
        bridge = layers_by_type["reconstruction_bridge"]
        supply = layers_by_type["verified_fbw_supply"]
        self.assertEqual(bridge["original_units"], 10)
        self.assertEqual(bridge["wb_units"], 10)
        self.assertAlmostEqual(bridge["unit_cost_rub"], 100.0, places=2)
        self.assertEqual(bridge["status"], "active")
        self.assertEqual(supply["original_units"], 3)
        self.assertEqual(supply["wb_units"], 3)
        self.assertAlmostEqual(supply["unit_cost_rub"], 100.0, places=2)

        # A second readiness check must now report "already applied" and refuse a new Apply.
        readiness_after_apply, _, _ = read_fifo_reconstruction_v67_readiness()
        self.assertFalse(readiness_after_apply["ready"])
        self.assertEqual(readiness_after_apply["already_applied_run_id"], run_id)

        # 3) Rollback: restore from the pre-Apply backup and confirm the FIFO layers this
        # Apply created are gone again (the concrete, checkable "restored the prior state"
        # assertion -- not just "no exception").
        rollback_result = rollback_retroactive_fifo_reconstruction_v67(run_id, f"ROLLBACK {run_id}")
        self.assertTrue(rollback_result["ok"])
        self.assertEqual(rollback_result["run_id"], run_id)
        self.assertEqual(rollback_result["restored_from"], backup_path)
        self.assertTrue(Path(rollback_result["safety_backup"]).exists())

        with connect() as conn:
            run_row_after = conn.execute(
                "SELECT status FROM fifo_reconstruction_apply_runs WHERE id=?", (run_id,)
            ).fetchone()
            layers_after = conn.execute(
                "SELECT COUNT(*) AS n FROM finished_goods_cost_layers WHERE nm_id=?", (NM_ID,)
            ).fetchone()["n"]
            # The pre-Apply evidence rows (stocks/costs/wb_fbw_*) must also have come back,
            # proving this was a real whole-database restore and not a partial patch-up.
            stocks_rows = conn.execute(
                "SELECT COUNT(*) AS n FROM stocks WHERE nm_id=?", (NM_ID,)
            ).fetchone()["n"]
            supply_rows = conn.execute(
                "SELECT COUNT(*) AS n FROM wb_fbw_supply_confirmations WHERE supply_id=5001"
            ).fetchone()["n"]
        self.assertEqual(run_row_after["status"], "rolled_back")
        self.assertEqual(layers_after, 0, "rollback must restore the pre-Apply (zero-layer) state")
        self.assertEqual(stocks_rows, 2)
        self.assertEqual(supply_rows, 1)

        # And the readiness gate should be ready to Apply again (same candidate figure),
        # since the restored database is byte-for-byte the pre-Apply state.
        readiness_after_rollback, _, _ = read_fifo_reconstruction_v67_readiness()
        self.assertTrue(readiness_after_rollback["ready"], readiness_after_rollback.get("reason"))
        self.assertEqual(readiness_after_rollback["candidate_loss_units"], 10)
        self.assertEqual(readiness_after_rollback["already_applied_run_id"], 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
