# Secondary schedules implementation

Base: 1d1169a. No production or gateway changes in this worktree.

1. Persist owner-configured, disabled-by-default sync/PDF schedules in the main
   encrypted-backup SQLite database. Immutable request receipts and attempt events.
2. Strict fixed HTTPS Gateway membership client; canonical account IDs, no
   redirects/proxy inheritance, five-second total check and one-KiB response.
3. One due job per tick, UTC due slot with deterministic UUID and expiring lease.
   Never create ACLs. Delegated actor must currently be owner/manager AND pass
   existing ACL as manager. Freeze seller, credential, generation and revision.
4. Prepare selected child privately, then repeat membership check. Commit selected
   snapshot, bridge receipt and schedule success under one BEGIN IMMEDIATE after
   fresh membership result, lease, generation, credential and ACL rechecks.
   Conflict/failure retains prior snapshot and bounded retry; lost lease fails closed.
5. Local scheduled PDF uses fixed due UTC date/window and existing bounded archive;
   no delivery. Existing selected-shop download checks remain authoritative.
6. Owner-only configuration API with optimistic generation and immutable UUID
   replay. Read access obeys existing shop ACL; no configuration implies disabled.
7. Unique external pytest basetemp; source/SQL/restart/concurrency/revocation tests,
   real child PDF/hash/isolation and native backup/restore evidence, independent
   review and root integration/native/runtime acceptance before closure.

Trusted boundary: forwarded account/role originate only at authenticated Gateway;
the tenant API is not directly public. Runtime checks use the existing tenant-scoped
internal secret. Downgrade after new state requires preserving current data and
restoring a verified compatible backup, not just swapping container images.
