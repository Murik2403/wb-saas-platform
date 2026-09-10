# Project audit and first repair batch

Goal: inspect the local Vite/tenant API, shared business logic, gateway and deployment; reproduce defects on isolated fixtures and repair confirmed data correctness failures.

1. Establish baseline: tenant and gateway pytest, lab API tests, both frontend builds; inspect launch scripts, deployment authentication, backups, synchronization and write paths. Never run external agent actions or restore the live database as a test.
2. Add regression tests in lab API tests for costs without a catalogue, non-finite monetary input, cache invalidation during an in-flight build and after successful writes. Repair api_server.py and dashboard_cache.py.
3. Introduce a shared OS file lock in tenant-app/sync_lock.py used by sync.py and the API runner. Test contention from separate processes and release after exceptions. Lock before starting work or recording a run; competing API requests return 409.
4. Inspect live SQLite read-only for integrity and data coverage; record architectural and operational gaps separately from repaired bugs in docs/PROJECT_AUDIT_2026-09-09.md.
5. Run the affected suites and builds; restart the owned local API and check read-only routes. Document skipped production/Docker and external-service checks explicitly.

Execute in this task, as requested by the user. No deployment or changes to marketplace prices, campaigns or deliveries.
