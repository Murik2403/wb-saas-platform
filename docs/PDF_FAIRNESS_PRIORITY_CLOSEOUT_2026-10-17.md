# PDF priority review closeout — 2026-10-17

Branch `claude/release-closure-20261017` merges committed `7648514` into `885310b` (merge `e21fb17`). The merge preserves the `243fb94` landing and the `a180ecd` data-quality optimization. No deployment or Docker run was performed for this closeout.

## Changes

1. Interactive PDF rechecks cancellation/background priority while holding the granted slot, immediately before building. A beacon appearing during slot acquisition refuses the request and releases both locks.
2. The parent scheduler tracks only successfully PDF-busy-deferred secondary-shop jobs. Tracking includes database path, schedule ID, generation, original due and attempt count. Revalidation checks enabled state, PDF kind, current membership, ACL and seller identity. A short-lived child never owns the persistent priority beacon. Primary and secondary queues cannot clear each other's priority; draining, disabling, reconfiguring, revoking access or failing the deferred job clears secondary priority on the next scheduler tick. A process exit releases the same OS beacon lock covered by the existing dead-process regression.

No schema migration, change to financial calculations, role authorization, slot limit, queued attempts or the 50-second interactive deadline is introduced. `run_one()` retains its existing boolean interface.

## Verification

- Red stage: four focused new tests failed before implementation (slot-grant counterexample and absent secondary tracking).
- Final tenant-app focus: `test_pdf_budget.py`, `test_pdf_fairness.py`, `test_shop_schedules.py`, `test_shop_report_assets.py`: **85 passed, 1 skipped**. The skip is the existing platform-dependent subprocess check.
- Tenant API `test_report_pdf_budget_route.py`: **6 passed**.
- Two temporary source mutations were killed by the regressions: removing the slot-grant recheck; removing secondary priority from the beacon retention condition. Original source restored after each mutation.
- Graphify AST refresh completed: 13,484 nodes. Three pre-existing Gradle parser warnings remain.

The earlier full focused run had three failures because its primary-only scheduler stub did not expose the newly added `pdf_waiting()` interface; the stub was updated to return false, and the full run repeated successfully.

## Remaining acceptance

Real 20/25/30-container load, production rollout and external provider delivery are owned by the release coordinator. Existing negative acts remain unchanged. This source/test result does not claim those checks passed.
