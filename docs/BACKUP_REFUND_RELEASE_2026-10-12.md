# Backup/refund release acceptance — 12 October 2026

Merged `claude/backup-refund-bench-20261016` / `75d5f1e` by fast-forward from `2349b7c`; independent review corrections committed as `b13fa97edafa81d814ab1464a8e979abc85e7a62`. Gateway and provisioner deployed from that committed SHA. Subsequent documentation commits do not change runtime revision.

## Corrections before deployment

- Refund attempts use a persisted exact provider description, not amount-only webhook/history matching. Incomplete or malformed lists fail closed; pagination is bounded and validated. A different request reference cannot borrow an existing attempt. Later/parallel 4xx does not release an uncertain reservation. Missing/wrong direct response marker retains the reservation. Long request references are rejected, not silently truncated. New fields migrate additively; legacy attempts retain their request body.
- Directory walk errors reject a snapshot instead of silently omitting data. Snapshot helper cleanup removes its anonymous volumes while preserving the named source. All temporary cleanup paths recognize generated names and the directory group; foreign/manual files remain.

Fresh source checks: gateway **668 passed, 237 subtests** including real local PostgreSQL concurrent reservation tests; provisioner **151 passed**; scripts **224 passed, 3 skipped**; historical COGS **12 passed**. New safety regressions were confirmed red → green.

## Production evidence

- Gateway image `sha256:6d98f9fda14bdb4b9c43d3a7b485f360617034800dc8357c3fdc85ffd710f72d`, UID10001, memory1024MiB.
- Provisioner image `sha256:3896f0371e06c430e280daaccd04944a0230df1d92bbe2eaa70f7e2640d9b50d`, appuser/UID10001, memory256MiB.
- Exact source hashes, live image/revision, preserved environment/mounts/networks/aliases/restart policies, no OOM/restarts verified. Refund columns/indexes exist, SQLite integrity is okay, open intent count at acceptance **0**.
- Five tenant IDs and images remain unchanged at `e7b1582`, limits1536MiB, all public health responses okay. Future tenant image remains `mh-tenant:finance-e7b1582-7702ddf823e8` in runtime and effective Compose. Live finance90d HTTP200 **8.957s**, selected dates confirmed.
- Capacity remains5tenants/1920MiBreserved/admissiontrue; unauthenticated capacity denied. Docker INFO remains private. All16video/caption hashes, Range and security headers pass.
- Fresh encrypted online gateway snapshot decrypted and passed isolated candidate migration and old-image compatibility gates. Account/payment/refund table digests unchanged; provider not contacted, live data not mounted in rehearsals.
- Real Docker synthetic WAL gate:8snapshots during concurrent transactional writes,1after freezing, plus malformed-database failure. Integrity, transaction invariant, committed-before rows, permissions/layout, encryption roundtrip, narrow cleanup, named volume preservation and no anonymous volume leaks verified.
- Deployed demo backup: `demo-20261012_185937_907611-00fee4cbc1ab.tar.gz.enc`, **1.617s**, WBSAASBK2, mode0600, 3677916bytes. Independent decrypt/extract and integrity_check passed;119tables. Existing demo verifier accepted. No partial/stage files or anonymous-volume leak; unknown files preserved, known archives pruned **0**, normal14daypolicy used. Private restore copies removed; encrypted archive retained.

Sanitized evidence: `deliveries/backup-refund-20261016/{image-proof,docker-snapshot-proof,gateway-copy-proof,deployment-proof,manual-demo-backup-proof,final-acceptance-proof}.json`.

## Compose caveat and safe procedure

Dry-run of ordinary original-project `compose up --force-recreate provisioner` would remove17retained service-labelled rollback containers. It was not executed. Gateway was replaced with preserved runtime settings; provisioner was created through same-project `compose run -d --no-deps --no-TTY --name wb-saas-provisioner-1 --use-aliases provisioner`. Compose run disables the service restart policy, so the original policy was explicitly restored through Docker update and checked before acceptance. An initial candidate lacking this policy was automatically rolled back, then resumed safely. All17prior provisioner rollback IDs remained present.

Effective Compose image pins and the existing daily-demo-refresh setting are preserved in non-secret overlays. Future replacement must review the dry-run and retain rollback objects; blindly using original-project compose up is unsafe while those service labels remain. Rollback to old gateway code is forbidden if open refund reservations exist or their count cannot be verified.

## Remaining external checks

No real payment/refund or real YooKassa test-shop transaction was made. Provider behavior is covered by official-contract checks and stateful fakes; perform test-shop acceptance before the first operational refund. The density benchmark is a local8GiBVM result through20tenants;25/30figures remain extrapolations, not production simultaneous-load evidence.
