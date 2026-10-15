# Audit follow-up — 15 October 2026

This records evidence after application revision `73c6a34b8ee2940a25fe40ac8c65c2e06672c02d`. Report/FIFO read-memory fixes through committed **`d0141b2220242f003f00edf8d88bd94b6202d98f`** passed actual two-module copy acceptance and were deployed to all five tenants. Final tenant gate: **15 October 2026, 15:34:29 UTC / 18:34:29 MSK**. Gateway public-page revision **`33ab7e477a683f9d6cab500f8e89e8e8d7eb1b72`** was deployed at **16:53:15 UTC / 19:53:15 MSK**, then superseded by committed **`25ae99a3a148d041ed4a24d587bca6389cd858c3`** at **17:26:37 UTC / 20:26:37 MSK**; provisioner remains73c6a34. Earlier negative attempts are preserved. The image capture for d0141b2/controls73 is owner-authenticated and copied/read back offsite; after gateway25ae99a it is a historical source inventory. **Its actual native linux/amd64 image-load acceptance now passed on the new guest; full application/data/OS-reboot recovery remains open, and gateway25ae99a is not included in that archive.**

Public receipts named below are retained under `deliveries/audit-followup-20261015/` in the shared workspace. Copied customer rows, credentials and mail contents are not published.

## Backup alerts and SMTP

The exact alert wrapper called the expected gateway mailer with one fake-transport call and no real message. Actual SMTP authentication failed with **535**. Focused alert/cron/offsite tests: **44 passed**. This established a credential/configuration failure, not a missing invocation or proven mailer source defect (`backup-alert-diagnosis.json`, `backup-alert-focused-tests.log`).

A configuration-only SMTP rotation completed at **13:50:35 UTC**. Only the SMTP password changed; the gateway application revision/image stayed unchanged. Rollback container and private configuration backup were retained. Both the credential probe and replacement gateway runtime authenticated and reached **NOOP 250**. The receipt records `applicationCodeChanged=false`, `onlySmtpPasswordChanged=true`, `messageSent=false` (`smtp-rotation-live-proof.json`).

No customer/test email or SMTP DATA was sent. Authentication is verified; inbox delivery and actual failure-alert receipt are not established by this operation.

## Actual scheduled backup evidence

Read-only inspection at **13:53:34 UTC** found the daily attempt at **02:15:39 UTC**, verified at **02:18:20 UTC** on 15 October. Its persisted result reports local freshness, verified offsite copy and confirmed key escrow, no error and `alert=not_needed`. Encrypted groups: **control-plane, demo, mvvorokov, marketing, letoocean, alexdalex-msk and training-media**. Newest archives were approximately 11.6 hours old at inspection.

The hourly watchdog result at **13:45:01 UTC** reports `fresh=true`, no error and no required alert. Cron contains daily `15 2 * * *` and hourly `45 * * * *`, both using committed **73c6a34** runner. Execution timestamps above are UTC; do not reinterpret the cron numerals as Moscow time. These are persisted scheduled results, separate from earlier manual helper tests (`scheduled-backup-readonly-proof.json`).

This closes the previously pending first scheduled-firing observation within those receipt boundaries. It does not establish fresh-OS DR, independent key custody or email delivery.

## Report-memory defect and committed repairs

Live kernel/cgroup evidence identifies a Python child killed at **06:01:24 UTC / 09:01:24 MSK** inside the current mvvorokov container. API/resident and container init stayed alive. Report run **82**, definition **16**, began at 09:00:01 MSK and failed at the matching time with isolated metric **`wb_commission_rate`**. A Docker OOM flag alone would not identify that child.

The commission builder fetched every financial column, including unused `raw_json`, before filtering/copying rows. Commit **`61d9068068512bd7d606fff27013d77eb3d8d0ad`** projects financial/catalog payloads out at the database read. Commit **`c6b1378364a7e5b0c9a669fb413a7a604839db65`** applies existing `omit_raw=True` to eight remaining direct reads. All **10** direct report reads now project before fetching. Financial formulas, date/category/return behavior, timeouts, isolation, admission, PDF slots and memory limits are unchanged.

Normalized committed `metrics.py` SHA-256: `d22343775ae4f053f2e468a22f9c8580a1d4d80db3119ec4379efe934208e6bd`.

Verification of c6b1378:

- `tests reports/tests`: **1,270 passed, one skipped, 62 subtests**, 141.86 seconds; separate `agents`: **19 passed**. Total **1,289 passed** (`all-metric-projection-review-ready.json`, full logs).
- Seven populated real-SQLite cases deny raw-column access with SQLite authorizer and compare title, summary and rendered PNG against full-read references. Before the eight-read repair: six failed / one passed; afterward all seven pass.
- Ten independent in-memory mutations reintroducing payload access at each direct call are caught by the SQL authorizer (`all-metric-mutation-proof.json`). Numeric assertions remain intact; mocks only accept the existing projection keyword. The allowlist test now uses AST so calls with keywords remain covered.
- Graphify updated; diff check passed. Source tests do not establish production memory headroom.

### Follow-up d0141b2: FIFO read working memory

The second payload path was `read_financial_fifo_reconciliation`: `SELECT *` plus `fetchall()` retained every full financial payload for the period, even though ordinary matching uses only SRID. Unlike ordinary rows, shared warehouse bindings and imported historical documents authenticate the entire original row with `financial_source.fingerprint`; dropping their raw fields would change financial provenance decisions.

Commit **`d0141b2220242f003f00edf8d88bd94b6202d98f`** changes only that database-read adapter: iterate financial/event cursors; retain ordinary payloads as compact JSON containing exactly the existing `_json_srid` interpretation; preserve full original financial rows for **every** referenced ID in `warehouse_financial_bindings`, `warehouse_financial_projection` and `migration_historical_documents`, irrespective of shop/active status. Protection IDs and data are read inside the same existing BEGIN snapshot. `_reconcile`, formulas, fingerprints and projection modules remain unchanged; stored source data is not rewritten.

New populated SQLite tests enforce streaming (no full-payload cursor `fetchall`), bounded retained payload sizes, exact complete output parity against original payload retention including malformed/uppercase/nested SRID and duplicates, and exact original fingerprints for all three protection tables with other-shop/inactive records. Four in-memory mutations are caught, including deletion of each table's protection. Existing actual shared-warehouse/manual-history/COGS suites pass.

Focused **56 passed**; full `tests reports/tests` **1,273 passed, one skipped, 62 subtests**, 115.02 s; `agents` **19 passed**. Total **1,292 passed**. Fresh tenant-api: **685 passed**, two warnings, 122.83 s (`d014-tenant-api-full-receipt.json`); source hashes were checked against committed d0141b2. Graphify updated, diff check passed. Receipt: `reconciliation-streaming-review-ready.json`. Reconciliation module normalized SHA-256: `4c80abd22ec59d2f11054402c55a6368e3b8968561d2ad48287eb2db575eff92`; metric module hash above is unchanged. Actual copy resource acceptance is recorded separately below.

## Preserved negative real-copy attempts

These are isolated copy acceptance attempts, not fresh-OS or density tests. Original source container/image/lifecycle and OOM counters were preserved in all three receipts; source OOM deltas were zero. Negative results are retained.

| Attempt | Actual result | Consequence |
|---|---|---|
| 1: `commission-copy-attempt1-negative.json` | Source `/tmp` snapshot could not fit the database; operation and first cleanup reported errors. No render helper ran. | Retire the design. Keep the owned-inode record for parent cleanup/reconciliation; this receipt does not establish successful cleanup. |
| 2: `commission-copy-attempt2-negative.json` | Preflight wrongly required unused legacy `/app/tenant-app/data` anonymous mount to match actual `/app/data`; rejected before helper creation. Private cleanup succeeded. | Select only actual primary mount and supported effective data-dir; do not mount/change the unused alias. |
| 3: `commission-copy-attempt3-negative.json` | **336,531,456-byte** online copy passed integrity. Old/new commission projection and PNG exactly equal; process `ru_maxrss` **965,292 → 317,660 KiB**. Full 21-metric PDF plus **1024 MiB ballast** still caused **one helper OOM / one OOM kill**, despite valid **1,075,111-byte** PDF in **151.722 s**. | Overall **NEGATIVE**. Source unchanged; private copy and own helpers removed, no cleanup error. Motivates c6b1378 and full per-metric profiling/parity. |

Process `ru_maxrss` is not cgroup kernel peak. Sampled cgroup maximum is a lower bound; attempt 3 had no available kernel `memory.peak`. Ballast approximates API/resident consumption and does not recreate the actual full deployed runtime.

## Four-phase attempt 4 — NEGATIVE

Four fresh render containers: old commission; candidate commission; old full 21-metric PDF without ballast; candidate full 21-metric PDF with unchanged **1024 MiB ballast**. Limits remain **1536 MiB, no swap, one CPU, network none**, without runtime credentials and with finite deadlines. Snapshot is online SQLite backup with SQL source read-only in its own helper; WAL shared-memory support requires RW source-mount capability.

All **21 actual successful metric-worker outputs** (title/summary JSON and PNG) are hashed before deletion. Strict public schemas, exact per-metric hash equality, zero new helper/source OOM, source lifecycle preservation and confirmed removal of owned helpers/private data are required. A successful PDF alone cannot pass. Child RSS observations are explicitly cumulative waited-child high-water values, not exact independent peaks for later workers.

Independent harness review: **36 pure tests passed** (`commission-fourphase-independent-review.json`). Operator SHA-256: `479b56961590335ac7576f4496182167873d8ad9962ecbea28cde70b59ea6b6c`; probe: `f527bdf56edfcadcde5f1f489c95a21d49420216fed459f0cea892dd2436e10f`.

Parent's actual-run update reports **all 21 title/summary and PNG hash pairs equal, but one ballast OOM kill**. Therefore attempt 4 is **NEGATIVE** and c6b1378 is not resource-accepted. Some builders still reach approximately 570 MiB without ballast; direct raw-read projection alone is insufficient. Parent retains/reconciles the formal receipt and cleanup evidence; those details are not independently asserted from this interim update.

## Two-module attempt 5 — actual PASS

The actual receipt at **15:15:28 UTC** tests committed d0141b2 with **both** the metric and FIFO read adapter mounted read-only in candidate phases; baseline phases retain both original modules. The second committed module hash is mandatory in stage metadata and acceptance proof. The **336,572,416-byte** online SQLite snapshot passed integrity; source lifecycle and OOM counters were unchanged, own helpers/private copy were removed with no cleanup errors (`commission-copy-acceptance-proof.json`). Prior negative receipts were not overwritten or relabelled.

All 21 successful metric title/summary and PNG hash pairs match exactly. The two complete PDF byte hashes differ; byte-identical PDF files are not claimed. Candidate full PDF is **1,076,218 bytes**, baseline **1,076,220 bytes**. Unchanged **1024 MiB ballast** remained alive and helper/source OOM deltas were zero under the same **1536 MiB / CPU 1 / no-swap / network-none** constraints.

| Phase | Seconds | Process self ru_maxrss KiB | Sampled cgroup max bytes | New OOM / kills |
|---|---:|---:|---:|---:|
| Baseline commission | 4.088 | 982100 | 924434432 | 0 / 0 |
| Candidate commission | 2.989 | 317156 | 209518592 | 0 / 0 |
| Baseline full 21-metric PDF, no ballast | 146.992 | 166128 | 1015406592 | 0 / 0 |
| Candidate full 21-metric PDF, 1024 MiB ballast | 162.839 | 164636 | 1553375232 | 0 / 0 |

Kernel `memory.peak` was unavailable; sampled maxima are lower bounds, not kernel peak claims. Parent/child ru_maxrss are different measurements and child observations are cumulative waited-child high-water values. Ballast is an approximation of existing API/resident memory; this PASS does not establish full deployed-runtime capacity or 20–30 concurrent tenants.

## Actual committed release and final live verification

`commission-release-proof.json` records a successful application delta of exactly **two files**: `tenant-app/reports/metrics.py` and `tenant-app/db/financial_fifo_reconciliation.py`, from committed d0141b2 over source73. Before the first swap, all five fresh retained encrypted tenant rollback backups were present. All **seven** tenant/control swaps reached `verified`; rollback containers/configuration remain available. Tenant frontend hashes, environment apart from expected build identity, mounts, aliases and resource limits were preserved.

Future tenant pin is **`mh-tenant:commission-d0141b2-0348ab4afa15`**. At that release controls were recreated for configuration only and retained source73; provisioner alias/health was verified. The later gateway33 and gateway25ae99a releases are recorded separately below.

Final live proof at **15:34:29 UTC** (`commission-live-final-proof.json`) confirms all five tenants have committed d0141b2 and exact hashes of both modules, OOM/oom_kill/restarts = 0 in the replacement containers. Public tenant health endpoints returned HTTP 200 first try; main `/healthz`, `/login`, `/training` returned 200. SMTP runtime again authenticated with NOOP 250, `messageSent=false`.

| Tenant | Current immutable image |
|---|---|
| demo, mvvorokov | `sha256:ed2e8b9112a771f8af84e00531616a2935962597e9bfaae83a3f743b8e3a19d4` |
| marketing | `sha256:b579582e9533989bd5b440e8e64332e908268d1d72e076ea5a190631907696c3` |
| letoocean | `sha256:93c4a6cefadf542872c4ab6ba1cde4a7f74cf1da9d43518f0a382527b11b6785` |
| alexdalex-msk | `sha256:61347d5be344617b3391198f8ce35097dc27b1dc0b001f9ae49d072fb2409096` |

## Captured d014/control73 images and new OS: actual intermediate results

Current-image receipt `deliveries/current-image-escrow-20261015/owner-proof-2616cd4e422549ae90b325b19a423633.json` proves successful owner-side **MHOFFSITE2 authentication** of a new encrypted copy for **8 unique images / 9 application roles**, tenants d0141b2 and controls73. Source stream: **456082432 bytes**, SHA-256 `36e6087e4210ed764726accc7d9868c41454aeb203027e14da41fb3878bdce01`; encrypted copy **456083206 bytes**. No plaintext file was written, no owner RSA private key uploaded, original escrow unchanged. **`docker_load_verified=false`, `offsite_readback_verified=false`, `full_server_DR=false`.** This application-role inventory does not cover the entire host, such as Postgres/Glitchtip.

Those booleans are the owner receipt's original phase. A subsequent offsite receipt below closes copy/readback for the same capture. The archive predates gateway33 and gateway25ae99a and must not be described as the exact current full image inventory after those releases; new-gateway capture/load/offsite is separate.

`deliveries/local-dr-stand-20261015/evidence/clean-guest-boot.json` records an actual separate **Ubuntu 24.04.5 LTS** boot, kernel **6.8.0-142-generic**, MemTotal **4009852 KiB**, `dockerInstalled=false`, `clientDataTransferred=false`. The first public Docker bootstrap failed with package HTTPS download timeouts (APT exit100); no completed Docker installation is claimed. The owned guest was shut down normally, without customer data. A separate e1000 retry guest actually installed Docker29.7.2 (local-dr-e1000-20261015/evidence/docker-bootstrap.json). Its private-network-oci.json records the same public-proven TCP/external UDP DNS/default DNS targets blocked with restrict=on. The old loader refused before Docker load because seven legacy configurations cannot identify eight OCI root IDs. NEW OCI loader review:134 tests plus22 subtests; actual load PASS is recorded below. The guest was then independently inventoried and shut down normally without starting applications. All previous negatives remain preserved. This is a clean guest boot, not application/data restore, reboot recovery, DNS/TLS, login/UI or complete server DR.

## Claude queue-aware harness review

Branch **`claude/pdf-slots-load-20261020`**, HEAD **`27070ca8630c9a3be98da378f3c6cf429f0fa830`**, base d7f3729, is **not merged**. Read-only merge analysis found no application-code changes/conflicts against d0141b2; original focused tests passed **36/36** without Docker stress. Independent fault inputs reproduced false PASS with missing kernel peak, no API dashboard work, and incomplete per-tenant driver coverage.

The delivery-only draft `claude-slots-observability-DRAFT.patch` adds mandatory observed per-tenant counters/coverage, sampler error propagation and driver thread/subprocess failures; original plus added focused faults are **56 passed**. The draft is not applied and does not yet solve every job/run identity, native runtime/shared-mount verification, admission ordering or ownership/cleanup requirement. Historical 2–4 smoke receipts stay as recorded; they are not retroactively accepted under stronger checks. New queue-aware **20/25/30 are NOT RUN**, and earlier strict negative 25/30 results remain negative.

## Remaining separate gates

Inclusion of the latest gateway25ae99a image in image escrow and independent new OS **application/data restore/reboot/DNS/TLS/login/UI recovery**, strict simultaneous density and a separate queue-aware 20/25/30 profile remain open. Owner authentication, encrypted offsite readback and clean guest boot are the intermediate successes above. Queue-aware acceptance preserves prior strict simultaneous 25/30 negatives. Prepared tooling, a clean OS boot or DinD is not complete fresh-OS recovery.

Production full-power permission expired at 07:00 MSK; no daytime density stress is included. Claude receives scripts/tests/docs-only preparation and local 2–4 smoke, without deploy or expense.

Independent key custody, YooKassa test-shop payment/refund proof, real Ozon/Performance data, accountant validation and physical mobile/signing/stores retain earlier owner-dependent boundaries. Telegram stays disabled. Unknown archives and owner-excluded Defender are outside this follow-up.

## Actual current-image offsite copy

Owner-authenticated ciphertext **456083206 bytes** was copied to one new immutable UUID object in RF Timeweb S3 and read back in full. SHA-256 matches the owner proof: `138a87224516cb7cd69404a7839898cd1a5da1f7adf6ea00f61d5fb3005d3313`. Source cipher and all nine recorded runtime identities were preserved; no objects were deleted, no RSA key uploaded, no plaintext archive written. Receipt: `deliveries/current-image-escrow-20261015/offsite-current-proof-5e45a2a72e484b7ebe4d65d9a10ae198.json`. This closes offsite copy/readback for that d014/control73 capture, not image load or whole-server DR.

## Actual gateway33 public-page release and independent live-browser check

The first gateway attempt failed **before any swap**: native backup succeeded but its observer used host `BACKUP_DIR` instead of container `BACKUP_MOUNT`. `release-proof-54ebfeb3ea4c4f93be36aff83c3d5a41.json` and `backup-attempt1-diagnosis.json` are preserved. Only that observer line changed; backup validation, image/source checks, retention, deadlines and rollback gates stayed intact. Independent retry review plus meaningful host/container-path child-code fault fixtures: **91 pure tests passed**.

`deliveries/audit-followup-20261015/compliance-mobile-review/release-proof-719e903bd399470d9cc2a132ed15cd69.json` records successful deployment from exact committed **33ab7e4** Git bytes for three runtime files: compliance JSON, fallback loader and template. A fresh retained **102261-byte** native encrypted control-plane backup was verified before the durable replacement journal and swap; pruning was disabled only in that one-shot observer process. Current SMTP environment, mounts, aliases and resource/security limits were preserved. Effective Compose changed only the gateway image; original gateway and private configuration backup remain available for rollback. Five tenants/provisioner were preserved.

Gateway immutable image is **`sha256:d524dc27b0a9f28cb02bc317097b34eb0212cf0be2f556c29333269903e0575b`**, container **`1e9b2c00cc691cb0db7817c01eb9fefc8f78384df561b49053d561cf920c095f`**. Parent release gates confirm all three runtime SHA checks, new gateway OOM/oom_kill =0, HTTP200 on health/compliance/login/training, all16 video/source/track entries, SMTP authentication/NOOP250 and `messageSent=false`. Complete gateway suite: **676 passed, 243 subtests**, two deprecation warnings.

Independent **actual-site** Chromium inspection visited `https://marketshelper.ru/compliance` in a fresh unauthenticated browser context, choosing only necessary cookies. All **12 width/theme combinations** (320/375/390/620/768/1440px × dark/light) have horizontal overflow0, JavaScript page errors0, five flow cards, four navigation links with existing targets, record77-26-574895/direct official link, Telegram disabled and no obsolete status/technical-copy phrases. No login, mail or infrastructure change was performed by the reviewer. Gateway identity above is taken from the parent release receipt, not an independent SSH inspection. Public RKN order/registration dates are not newly asserted: current direct registry fetch returned403, so the page publishes the owner-confirmed record number/link.

Live receipt and screenshots: `deliveries/audit-followup-20261015/compliance-mobile-review/live-719e903b/live-browser-proof.json`, `live-390-light-viewport.png`, `live-390-light-full.png` (plus all12 full-page captures). Incident wording is backed by the official primary-source text of article21(3.1), law152-FZ: `https://mintrud.gov.ru/docs/laws/130` (initial24h, investigation72h).

## Actual gateway25ae99a storage removal and light-theme hotfix

`deliveries/audit-followup-20261015/compliance-storage-removal-light-fix/release-proof-f7f3186425e54d8dbfd6946ac870888c.json` records actual PASS from committed **25ae99a3a148d041ed4a24d587bca6389cd858c3**, at **17:26:37 UTC**. The whole public storage section and navigation were removed at the owner's request. Home copy is unchanged; only scenario-card colors use existing theme tokens. Four committed runtime files were SHA-verified. New container `4bb187f1df9a95ffbd94751ab1fd15f8d4572d579fdfe38e4442a2e3748a152d`, image `sha256:ec1dca37b79bda5af5d26fcb5b8e3a6336b90f8e5d0d339f7deefdadf2231560`. Fresh encrypted control-plane backup:102261 bytes, SHA `c215518a09522e2a62d204541d12ba6e99832997bb0a6f56c57531139ee033df`, retained/pruned0; rollback retained. HTTP200 health/home/compliance/login/training; new OOM/kill0; SMTP NOOP250, no message sent. Environment, mounts, aliases, limits and unrelated services preserved.

Independent operator review personally repeated **80 pure tests**, including17 new negative controls for CSS, scenario counts, all removed storage markers and fourth-home payload/SHA/Git-blob/missing-file guards. Existing backup/identity/rollback/deadline protections unchanged. Parent's broader operator/transaction run:91 tests.

Independent live Chromium proof at17:28:14UTC: `compliance-storage-removal-light-fix/live-6fd7dd8473/live-proof.json`. Eight width/theme combinations320/390/600/1440px ×light/dark, both pages=16 route views, overflow0, JavaScript errors0. Home3 scenario cards, minimum measured text/link contrast6.071. Compliance has no data navigation, zero flow cards, no visible Timeweb, and retains official RKN77-26-574895 link. Gateway identity comes from parent receipt; reviewer did no SSH/deploy. The preceding gateway33 browser evidence is historical.

Latest gateway25ae99a is NOT in the historical gateway73 image escrow. New capture/authentication/load/offsite remains open. Historical d014/control73 OCI image load has now passed as recorded below; full application/data/OS-reboot recovery remains open. Independent key custody, unknown archives and owner-excluded Defender boundaries remain unchanged. New public legal/infrastructure wording requires owner approval before publication.

## Actual historical current-image OCI restore on the new OS — PASS

Parent executed the reviewed loader on the isolated native Docker29.7.2 guest. `deliveries/local-dr-e1000-20261015/evidence/current-image-load-oci-proof.json`, SHA **9afe3b6df0f4cb4190093a4755dfe0f26b8b72af94d3e078252871e06ff15224**, records **PASS**: exact8 authenticated OCI root IDs,9 newly created role aliases, native linux/amd64 child-ID/RootFS projection equality with actual source witness,291 checked descriptors.24 absent non-native descriptors are explicitly not restored. The legacy7-row projection rejection is preserved; identity rules were changed to validated OCI graph roots, not weakened to7 images.

Original owner ACL and full GCM authentication were retained. No RSA uploaded and no plaintext archive file written; Docker image layers were written inside the isolated guest. Application containers started0; production was not accessed by the loader; fullServerDR=false. The archive is the historical d014/control73 capture and excludes latest gateway25ae99a. New gateway capture/authentication/offsite/native-load remains a separate open gate.

Parent independent readback `evidence/image-inventory-private-shutdown.json` confirms8 images,0 containers and retained private networking, then requests shutdown. `evidence/last-owned-vm-stop.json` confirms the owned private VM stopped normally: PID4424 exited0 after1710.34 seconds, its four loopback listeners closed. This is image recovery on a new OS, not recovered application databases, genuine-key app boot, auth/UI acceptance after OS reboot or full host DR. No existing archive, key escrow or production service was removed.

## Latest Claude harness review98e3d5f

Read-only review of `claude/pdf-slots-observability-20261022`, HEAD98e3d5f, confirms141 focused tests and useful fixes for prior observation gaps. No merge/deployment/stress performed. Independent fixture counterexamples reproduce existing foreign-volume reuse/chown and falsely clean deletion when rm/probes fail; per-tenant admission still occurs too late. Detailed proof/reproducer/handoff: `deliveries/audit-followup-20261015/claude-obs-review-af0b3e366f6f/`. Required fixes and exact current base25ae99a are in AUDIT_CURRENT_STATUS.20/25/30 remain NOT_RUN for this strict harness. Landing582cdc3 is separate and not merged here.
