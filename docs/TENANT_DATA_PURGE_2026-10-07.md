# Owner-requested tenant data purge

Account deletion requests `purge_volume=true` only after owner authentication, current-password confirmation, CSRF verification and the account lifecycle/payment gate. Routine image upgrades and deployment do not call this purge and retain rollback containers.

Docker refuses volume deletion while any container references it, including an exited rollback, even with `force=true`. This was reproduced using an empty uniquely labelled disposable volume and two existing Alpine/BusyBox helpers: two references → 409; one stopped reference → 409; no references → 204. No real tenant data was selected.

The provisioner now serializes lifecycle operations in its single process and inventories at most 32 references before removing any cabinet. It allows only the canonical container and exited `<canonical>-rollback-*` / `<canonical>-refresh-rollback-*` containers with matching tenant slug, runtime network label, environment slug and exact data-volume mount. Mount sources must match Docker's volume mountpoints. Unknown names, foreign tenants, running rollbacks, undeclared auxiliary mounts and additional customer-data mounts fail closed before canonical removal.

The image-declared legacy `/app/tenant-app/data` anonymous volume is handled only when its name is exactly 64 lowercase hex characters and all its references belong to the same approved tenant inventory. Multiple historical auxiliary volumes are individually scoped; no other tenant's/shared auxiliary volume is removed. Inventory is checked twice before deletion and again between phases. All container/volume removals use `force=false`; Docker's in-use check remains the final race protection. Outside processes manipulating Docker bypass the in-process lock and can still cause a refused/partial purge; this cannot be made a Docker transaction by this handler.

Verified on the new source via real Docker SDK with an empty, labelled `deletecheck-*` tenant: canonical + stopped rollback, primary volume and declared anonymous auxiliary volume all removed successfully. All disposable resources were cleaned. Unit tests cover foreign/running references, incorrect mount paths/sources/types, count limit, changed inventory, shared auxiliary volume, idempotency and routine removal preserving rollback/data. No demo, mvvorokov or marketing purge was performed.

## Encrypted backup retention is separate

Successful live-volume purge does **not** immediately erase encrypted backups or legally retained payment records. Backups use the configured `BACKUP_KEEP_DAYS` retention, default 14 days; this handler does not shorten that policy, rewrite backup encryption keys or delete archive files. Archive expiry/retention-job evidence must be verified separately; a successful Docker purge is not proof that backup copies have expired. Gateway identity erasure remains subject to the distinct account-lifecycle/financial-retention policy.
