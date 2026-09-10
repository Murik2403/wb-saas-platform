# Dashboard display implementation plan

**Goal:** Correct the verified display defects without altering historical business records.
**Architecture:** A lightweight data-status endpoint reports connection presence and the last successful import independently of dashboard payloads. Existing frontend adapters accept both native ruble values and the older kopeck contract. Inventory keeps quantity separate from estimated coverage.
**Tech Stack:** FastAPI, SQLite, React, TypeScript, Vite, pytest.
**Spec:** docs/LOCAL_SCREEN_AUDIT_2026-09-09.md

## Tasks

- [ ] Add regression tests in `mh-redesign-lab/tenant-api/tests/test_display_contract.py`: no successful sync must not be fresh, failed latest runs must not advance successful timestamp, 66 units with unknown days must remain 66 units, zero days must remain zero.
- [ ] Add `/api/v1/data-status` in `tenant-api/api_server.py`, returning `connected`, `state`, `updated_at`, `lag_minutes`; read only successful sync_log rows and use Moscow timezone for legacy naive timestamps. Missing/malformed dates are stale.
- [ ] Use that endpoint for every non-mock frontend page; unavailable status must show unknown freshness, without inventing a current timestamp. Correct campaign field/unit mapping and preserve all returned campaigns.
- [ ] Return inventory quantity, retain nullable coverage, and display units independently. Replace report heading with a stable description. Show a missing-cost notice on financial/product screens.
- [ ] Verify pytest, TypeScript/Vite build, API payloads and browser screens including period changes. Prepare local masked token entry using the existing Windows keyring; never echo or put credentials in code or chat.

## Constraints

Do not invent costs, clear FIFO guards, apply advertising decisions or update historical records. Execute in this task; user already authorized implementation. Credential entry remains with the user.
