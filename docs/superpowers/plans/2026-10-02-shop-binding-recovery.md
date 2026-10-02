# Primary shop binding recovery Implementation Plan

**Goal:** Accept the real browser JSON binding request and recover safely from refused or unverified binding responses.

**Architecture:** Normalize bootstrap headers with Headers.set. Keep strict identity checks; on failure invalidate transport and notify ShopContextBoundary to unmount stale children and offer a fresh session bootstrap. Never substitute another shop.

- [x] Reproduce duplicate Content-Type from apiHeaders(true), and stale mounted UI after failure with regression tests.
- [x] Correct header construction and expose refusal without losing boundary recovery.
- [x] Run frontend tests/typecheck/build and update Graphify.
- [x] Commit only owned files; build exact committed source; verify image contents.
- [x] Deploy demo and mvvorokov with verified encrypted backups, preserved rollback and 1536 MiB; pin provisioner image.
- [x] Verify live health and finance/stock, record deployed revision.
