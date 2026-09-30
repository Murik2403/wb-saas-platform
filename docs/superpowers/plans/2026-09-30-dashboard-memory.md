# Dashboard memory acceptance repair

Observed: production tenant API was killed after Sept 1–19 prewarm followed by parallel production/overview/manufacturing reads. Existing rehearsal used 2 GiB instead of production 1.5 GiB. Existing DashboardData cache serializes its own misses, but retains eight periods and builds the next before eviction. Context cache additionally retains a dataset and deep-copies every DataFrame per read.

1. Keep one read-only heavy period snapshot and discard prior snapshot before a different-period build; compact response caches retain normal behavior. Clear generation still prevents stale publication.
2. Cache and deep-copy only small context metadata; obtain DashboardData through the shared bounded snapshot cache and share its read-only reference. Verify compute_today does not mutate source frames.
3. Add realistic large DataFrame concurrent regressions: one expensive build at a time, no DataFrame deep copies, small metadata isolation, pre-build eviction and invalidation.
4. Provide a private-container stress probe requiring network none, Traefik disabled and exact 1.5 GiB. Prewarm Sept 1–19 then load production/30d overview/today/finance/manufacturing concurrently; record actual memory usage, process exit/restart/OOM status. Parent runs before/after on restored backup fixtures, builds/deploys after gates.

No memory-limit increase, production writes, altered accounting rules, or broad table-reader change. Full-table dashboard transient allocation remains a measured risk; report exact clone results before acceptance.
