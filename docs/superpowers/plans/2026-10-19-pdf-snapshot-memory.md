# PDF snapshot memory implementation plan

**Goal:** Avoid eagerly duplicating all dashboard frames for every PDF metric while preserving metric isolation.
**Architecture:** In pandas 3, DataFrame shallow copies use mandatory copy-on-write. Deep-copy metadata with a memo of shallow frame copies. Older pandas retains the existing full deepcopy. No global pandas settings change.
**Constraints:** No financial formulas, limits, slots, gateway or production changes. Base ec13bc1; independent worktree.

- [x] Prove frame buffers are shared before mutation, metric writes cannot alter the cached snapshot or another metric, and nested metadata remains independent. Verify older-pandas fallback.
- [x] Implement the snapshot-copy helper and use it in report_worker.
- [x] Run focused isolation tests, existing all-21-metric PNG/metadata equivalence and full PDF text/page equivalence.
- [x] Measure eager-copy versus new-copy peak RSS in fresh processes on a synthetic dashboard; report scope honestly. This alone does not prove the 243 MiB full PDF fits 768 MiB.
- [x] Update graph and commit only scoped files.
