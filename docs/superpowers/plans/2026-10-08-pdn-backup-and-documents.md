# PDN internal documents and recovery archive completion

**Goal:** Deliver internally consistent operator documents and deploy independently recoverable erasure-ledger backups with verified age-based cleanup.

**Architecture:** Readonly consistent SQLite snapshots are encrypted to the owner's existing RSA public recipient; the private key stays on the owner's computer. Immutable snapshots are uploaded and checked in S3. Cleanup consumes a pinned manifest, verifies sources again and retains a recovery floor; it cannot remove the live ledger, unknown files, legal holds or backup prerequisites.

**Tech Stack:** Python/SQLite/cryptography, existing rclone configuration, root scheduler with sanitized failures, reportlab for reviewable documents.

**Spec:** Owner request to finish items1–2; current `implementation-status.txt` in workspace deliveries/rkn-notification-20261008.

## Constraints

- No real account deletion, no Telegram disabling, no payment or marketplace writes.
- Only committed source is deployed. Never export keys, provider credentials or real financial values.
- Ordinary recovery copies:14days; preserve at least newest recovery per namespace. Explicit legal holds are excluded and documented, never inferred blanket indefinite.
- Deletion uses exact unchanged paths and checksums plus verified recovery evidence; root recursive deletes and broad `rclone sync/delete` filters are prohibited.
- Reports distinguish logical file/API removal from physical destruction of provider disks; do not invent an RKN receipt, signature, certification or assigned protection level.
- Loss of authoritative live erasure ledger remains failclosed; union of stale backups does not prove latest deletion intent completeness.

## Tasks

- [ ] Independent encrypted ledger snapshot, validated manifest, readonly union recovery; focused negative tests.
- [ ] Exact pinned retention manifests, recovery floor, exclusions and changed-source refusal; local and adapter-based remote tests.
- [ ] Deploy and verify encrypted ledger locally and in S3; restore on owner's local computer using existing private recipient.
- [ ] Wire periodic ledger backup and bounded retention jobs with locks and failure alerts; verify actual scheduler configuration and run results.
- [ ] Prepare final operational rules, threat/harm assessment and request/access/incident/destruction forms from verified facts. Render final package and inspect layout.
- [ ] Record cleanup receipt and remaining external Telegram/RKN signature requirements; full tests and source hashes before completion.
