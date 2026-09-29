# Nested raw procurement completion

Goal: expand final production demand through pinned nested BOM versions into raw material demand, subtract usable exact-variant dated intermediate FIFO stock once across all tasks, and preserve route/stock provenance in the preview fingerprint and confirmed procurement notes. Keep the calculation read-only; confirmation still creates internal draft orders only.

Steps:
1. Add pure route resolution and strict catalog/variant/cycle/depth/fractional guards; support raw-only legacy recipes unchanged.
2. Allocate ready intermediate stock chronologically using a shared reservation ledger across tasks and sibling branches. Respect pinned recipe versions and reject dangling references even when stock covers demand. Account for explicitly planned earlier intermediate output without counting its raw demand twice.
3. Expand only uncovered intermediate quantities into raw demands; carry root task, exact stage variants, BOM chain, and consumed-layer provenance into raw lines and readable UI.
4. Include catalog identities and every intermediate layer balance/date/source/provenance in the atomic input snapshot and stale-confirmation fingerprint.
5. Add meaningful tests for 3-stage chains, existing stock, wrong/future variants, historical recipe pins, multiple consumers, cycle/depth/missing recipe, and changed-stock stale confirmation. Run tenant/API/frontend/build/gates and graph update; commit scoped changes. Root handles built-image rehearsal and deployment.
