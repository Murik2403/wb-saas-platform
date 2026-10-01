# Cabinet features integration and release

**Goal:** Merge `claude/cabinet-features-20261001` into `codex/cabinet-ux-20260930` and deploy committed source, gateway before tenants.

**Architecture:** Preserve shop isolation, default-disable unreviewed tax estimate, retain current runtime settings and rollback containers. Reuse verified runtime dependencies only when byte manifests and build contracts pass.

**Scope:** User work order dated 2026-10-02; onboarding, monthly P&L, product groups/trends/price advice/history.

- [x] Merge branch without staging unrelated TrueStats review.
- [x] Review/add explicit route scopes and tests.
- [x] Gate unreviewed tax estimate off in UI and API; check comparison cache isolation.
- [x] Copy new API modules into both deployment Dockerfiles.
- [x] Run frontend/build, tenant-app, tenant-api, gateway suites and update Graphify.
- [x] Build exact committed source in isolated release checkout; verify source bytes and startup.
- [x] Verify encrypted control-plane backup/restore; rehearse and deploy gateway, preserve media/network/env.
- [x] Verify encrypted tenant backup/restore; deploy demo and mvvorokov, memory 1536 MiB.
- [x] Pin provisioner to verified tenant image; verify public health, new routes, cold custom timings and demo UI.
- [x] Record deployed revision, tests, business checks and limitations. No live WB price writes, fake sync success or tax-review approval.
