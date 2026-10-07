# Disposable PostgreSQL acceptance gate

Prepared financial runtime is uncommitted. Local Docker is unavailable, so the
real PG test currently skips. Root owns this gate and the later separate release.
Never run this test against a production or GlitchTip database.

## Dependencies

Use the already-built frozen P1 gateway runtime image by exact ID as the test
runner; `gateway/requirements.lock` includes `pytest==9.1.1`, `psycopg==3.3.5`,
`psycopg-binary==3.3.5` and `argon2-cffi==25.1.0`. No new dependency build or live
container pip install is needed. The helper DB uses an existing PostgreSQL image
by exact ID. Do not pull/install packages into live containers.

Copy only reviewed source files and tests to a fresh operator-owned temp tree:
`gateway/logic/{db,accounts,billing,growth,password_reset,account_lifecycle}.py`,
other existing gateway imports/config, and gateway/tests including conftest/base.
A tracked-source archive plus explicit new files is suitable; no `.env`, key,
production SQLite file or secret artifacts may be included.

## Operator sequence (Linux host, synthetic credentials only)

Set `POSTGRES_IMAGE`, `GATEWAY_TEST_IMAGE` to inspected exact existing image IDs,
and `SOURCE_DIR` to the fresh reviewed source copy. No production DSN/envfile.

```sh
RUN_ID=mh-lifecycle-audit-20261007
PG_NAME=$RUN_ID-pg
NET_NAME=$RUN_ID-net
docker network create --internal --label mh.audit=account-lifecycle "$NET_NAME"
docker run -d --name "$PG_NAME" --network "$NET_NAME" --network-alias postgres   --memory 256m --pids-limit 128 --label mh.audit=account-lifecycle   -e POSTGRES_USER=mh_test -e POSTGRES_PASSWORD=synthetic   -e POSTGRES_DB=mh_lifecycle_test "$POSTGRES_IMAGE"
docker exec "$PG_NAME" pg_isready -U mh_test -d mh_lifecycle_test
# Retry readiness up to30seconds; failure aborts before test execution.
docker run --rm --network "$NET_NAME" --memory 512m --pids-limit 128   --read-only --tmpfs /tmp:rw,nosuid,size=128m   -v "$SOURCE_DIR:/source:ro" -w /source --entrypoint python   -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/source/gateway   -e MH_LIFECYCLE_TEST_DSN=postgresql://mh_test:synthetic@postgres:5432/mh_lifecycle_test   "$GATEWAY_TEST_IMAGE" -m pytest   gateway/tests/test_account_lifecycle_postgres.py   -q -p no:cacheprovider --tb=short
```

Expected: `1 passed`, not `skipped`. The explicit DSN is rejected unless its DB
path equals `/mh_lifecycle_test`; keep the network private and credentials synthetic.
The test removes and re-adds lifecycle columns in this disposable DB to verify an
existing-schema upgrade, repeats migration, blocks unknown charge deletion,
resolves via verified payment data, and preserves original payment/claim linkage
after tombstone creation.

Cleanup only these created objects: verify their `mh.audit=account-lifecycle`
labels before removing the named helper with `docker rm -f -v`, then remove the
named network. Do not prune images/volumes and do not touch GlitchTip. Remove the
operator temp source tree only after verifying its absolute path is the created
workspace. No live payment, account deletion or provider API is invoked.

## Separate financial release requirement

The gateway image and host `scripts/run_billing_cycle.py` must be switched as
one maintenance operation. Drain/suspend the OLD billing job before enabling the
new deletion route; the old job has no durable claims and can still charge stale
rows. Apply the additive DB migration first, install the new host script, restart
verified gateway, then resume the billing timer. Preserve rollback and encrypted
backup/restore evidence. Do not use this uncommitted package in the frozen P1
archive or claim that passing source tests proves this production rollout.

Primary provider contract: [YooKassa interaction format](https://yookassa.ru/developers/using-api/interaction-format)
limits idempotency to24hours; [verified notifications](https://yookassa.ru/developers/using-api/webhooks)
and authenticated GET status are used to settle unknown outcomes. No elapsed-time
unlock or blind external retry is introduced.

## Executed gate result

Gate executed on isolated server helper `mh-lifecycle-20261007-7011dd`:
`1 passed in0.48s`. PostgreSQL memory256MiB, runner512MiB/read-only, source-only
read-only mount, internal network, no published ports and no production env or
mounts. PostgreSQL storage used tmpfs, so no Docker data volume was created.
Runner/helper/network removal was independently checked by their ownership label;
remote temporary source was removed after preserving the local proof. Overlay
source hashes still match the working tree. No production deployment occurred.

Proof: `../deliveries/account-lifecycle-20261007/mh-lifecycle-20261007-7011dd/pg-gate-proof.json`
(relative to the parent workspace's deliveries directory).

The successful artifact above was captured BEFORE the independent-review fixes
to terminal UPSERT/owned settlements/cron lock ordering. Its hashes attest to that
specific earlier snapshot. A refreshed gate is pending final review; do not use
that artifact to claim PostgreSQL verification for the revised working tree.

## Refreshed post-review gate

Run `mh-lifecycle-20261007-d68bd2`: **3 passed in1.00s**.
The controlled two-connection test actually observed `pg_stat_activity` reporting
Lock while cron held a stale due-list row. The writer committed a manual payment
extension, the blocked account UPDATE resumed, and locked eligibility emitted
zero provider calls. Deletion-request vs charge-claim concurrency also serialized
with only the permitted winner outcomes. Migration/retention/late-settlement passed.
Output markers: `PG_LOCK_WAIT_OBSERVED=true PROVIDER_CALLS=0`,
`PG_DELETION_CHARGE_SERIALIZED=true`.

All14 overlay source/test hashes match the revised working tree. This supersedes
the pre-review gate for release assessment. Same exact frozen gateway/PostgreSQL
images, internal network, PG256MiB/tmpfs and runner512MiB/read-only; no production
mounts/env/ports and no actual provider calls. Helper/container/network absence was
verified by label and remote temporary source removed after preserving proof.

Proof: `C:/Users/mvvor/Downloads/wbsaasplatform/deliveries/account-lifecycle-20261007/mh-lifecycle-20261007-d68bd2/pg-gate-proof.json`.
