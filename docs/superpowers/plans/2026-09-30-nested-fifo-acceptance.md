# Nested manufacturing FIFO acceptance — 2026-09-30

Scope: port the existing scoped plan `2026-09-25-nested-semi-finished-fifo.md` onto the verified cost-history release, preserve textile WIP, then complete the missing transactional and UI guarantees. This worktree owns manufacturing accounting only. Root integrates and deploys after encrypted restore and image rehearsal.

Implemented:
- Typed raw-material and semi-finished BOM components; exact SKU/variant identity, recipe-version pinning, DAG and 8-level guards.
- Every intermediate release produces its own FIFO layer. A downstream stage consumes exact oldest ready same-variant layers, including different historical BOM versions at their actual costs.
- Components, labor, depreciation, physical/pipeline deltas, immutable release snapshots and FIFO allocations remain in one write transaction.
- Persist producer provenance in `materials_json`, rather than recalculate new history at read time. Older external/opening layers remain explicitly unknown.
- Preserve the consumer movement variant after the FIFO helper handles a different component variant.
- Reject missing/deleted catalog identities before any nested posting.
- Dependency-safe cancellation restores exact allocations; producer cancellation blocks until its consumers are reversed. Retry returns the persisted snapshot; changed source-key payload rejects.
- React component/variant editor, cycle blockers, date-aware stock/quantity/layer-count/cost preview and disabled release on insufficient stock or fractional piece quantities. Server revalidates upon posting.
- API accepts the pinned child version supplied by the UI and supplies read-only exact ready-layer stock.

Verification before integration: full tenant/API/frontend tests, production frontend build, promotion-source gate, clean-clone gate after commit, diff check and graph update. Exact totals recorded in parent handoff. Existing bundle-size advisory remains. No production customer writes or deployment performed by this agent. Root must run built-image multi-stage release/reversal on an isolated restored database and live read-only probes.

Limitations: intermediate units must be whole pieces. Alternate routes, co-products and automatic multi-stage scheduling are not inferred; these are beyond the stated nested BOM/FIFO audit gap. Public marketing must describe the implemented exact accounting workflow.
