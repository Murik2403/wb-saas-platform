# Production audit status, 7 October 2026

## Approved permission boundary

Owner confirmed twice: managers retain product, price and production work. API credentials/connections, Telegram recipient, team membership, backups and data deletion are owner-only. Anonymous demo is viewer. Signed identity is short-lived, tenant/method/raw-URI/account/role bound; missing/tampered identity is rejected.

## Verified committed runtime releases

Latest follow-up: Claude branch integration deployed API and frontend together to all three tenants and provisioner from committed `0a89f63132ba6d4b76c46477370f9f6a8052af4b`; see `AUDIT_MERGE_RELEASE_2026-10-07.md` for current image IDs, tests and live evidence. The table below records the preceding releases; gateway4926f48 remains current. No earlier runtime hardening was reverted.

| Service | Committed source | Current immutable image |
| --- | --- | --- |
| Gateway | `4926f482c5368c1645d21a7a58bb734fb3b0647f` | `sha256:82b495cdbd817c2e0ea9f14d72fda5e2bc66102617b7883fd89e0e1e1a2a4b74` |
| demo, mvvorokov, marketing | `1e51eb51f48659537963029a76ec12ed2313ac8b` | `sha256:6a6fbe608219af62118263c8e92722c641ad8e30b34f31297959b1339639525d` |
| Provisioner | `73f31ed1a741e9b006ea89882101ddcb0d6dfce1` | `sha256:5da176bc1b79be2333411881fe5f83c57f87c2f74e8316e01b9d30355e540cf5` |

Builds used archived committed source. Encrypted backup restoration and rollback-image rehearsal preceded gateway/tenant switches. All three tenants retain their data, aliases and1536MiB limits; provisioner retains256MiB and the `provisioner` alias. Gateway retains1024MiB. Course mount and16 narrated lesson/caption hashes were preserved; range206 playback acceptance passed.

## Production acceptance

- Signed manager/viewer settings read200; unsigned owner/tampered role403; manager backup list, WB connection, Ozon connection and Telegram link-code403. Empty write probes did not change marketplace data.
- Owner browser after current tenant reload shows connected WB/Ozon and enabled owner credential controls. Current PWA update banner warns to save input and offers explicit update or Later; no automatic reload.
- Local nginx `/build-id.txt` on all three tenants returns the committed build and no-store. Demo also passes the public check; private tenant URLs require ForwardAuth and an unauthenticated request redirects to login. Service worker caches only successful non-private public resources, limits assets64 and retains one previous generation.
- Discovery proxy permits required read-only Docker discovery and rejects synthetic mutation methods403. Gateway/tenants share no writable-proxy network; Traefik has no raw socket and retains all3tenant networks.
- Host compose/env pins preserve the separated `wbsaas_control_api`, `wbsaas_docker_control`, `wbsaas_docker_discovery` topology, purpose-bound provisioner credential and unchanged tenant-scoped retained encryption keys.
- Cold September finance200 in5.624s. Six concurrent live reads200 without OOM/restart, peak about585MiB. Management/monthly under this stress took14–17s: a remaining capacity target, not a claim that all calls take seconds.
- Synthetic oversized gateway and tenant requests413. Provisioner unauthenticated read403/authenticated nonexistent read404; real Docker SDK container discovery passed.

## Backup recovery and host schedules

Fresh gateway/control-plane and3tenant encrypted snapshots independently restored; SQLite integrity checked. Local47historical plaintext files received exact-roundtrip encrypted replacements. Remote75reviewed raw files consisted of47matched historical archives and28remote-only files. Of28,25passed gzip/tar structural validation and3were preserved as encrypted quarantine, never asserted to be complete restorations.

Owner-side recovery used the local private RSA key and retained backup key: outer and inner hashes plus tar structure passed, temporary plaintext removed. Private key never uploaded. All75original remote objects were individually retired only after exact replacement/original SHA checks. Independent listing:0legacy raw objects,28new encrypted remote-only replacements; older encrypted archives retained. Retirement proof SHA256 `2f47f06ac998a783c3a3d0200e3721d1898d054205ec86de35875e2f7c479eac`.

Daily02:15UTC encryption/copy/check pipeline and hourly:45 freshness checker installed, replacing old sync/local jobs. Backup retention14days; deployment ZIP retention5. Alert path exists; no deliberate failure email/Telegram sent. Recipient recovery material is local owner-only under `.codex/secrets/marketshelper-offsite-20261007`; do not commit/disclose its contents.

## Financial lifecycle

Durable provider-operation claims block unsafe repeated charges and account purge while pending/unknown. Identity deletion retains payment/refund history and erases working identity/access. Late events cannot revive deleted access. Webhook-before-response terminal-state and stale-period charge races have regressions.

Gateway gate511passed,5local skips,203subtests;3new Postgres tests separately passed on isolated server. Two existing testcontainer cases remain locally skipped. Billing cron drained, exact host script installed root:10001 mode0640, canonical live-container command resumed at03:00UTC. No test charge, real deletion or manual reconciliation was executed.

## Test evidence and limits

Current PWA frontend gate578passed, TypeScript/build and standalone service-worker regression passed. Scoped provisioner purge source gate80passed. Earlier unchanged tenant-app/API clean gates1028/627passed (tenant-app1skip). Not all gates were rerun merely for documentation. MainJS remains about881kB (>500kB), despite lazy-Ozon reduction; further splitting remains.

Owner/manager boundary follow-up:155focused API tests passed,0failed. Signed manager price upload and production release reach only mocked backends; viewer is rejected without an extra backend call. Eight sensitive manager paths reject403. Telegram/team gateway tests7passed. The added test fixture creates its own database; no production marketplace writes were performed. These final changes are tests/documentation only and do not need another runtime rollout.

Scoped-purge provisioner deployed from committed73f31ed after clean-archive80tests and isolated health/auth/source rehearsal. All copied Python/lock hashes match committed source. Live valid Bearer200, absent/invalid403, Docker SDK ping/discovery passed; all3tenant public health endpoints remain ok. Full environment,256MiB limit, purpose-bound credential, exact control networks/alias and future tenant image pin were preserved. Stopped rollback was detached from control networks with reconnect metadata retained. No live tenant purge was executed. See `TENANT_DATA_PURGE_2026-10-07.md` for fail-closed ownership/reference checks and separate backup retention.

Proof artifacts live outside Git in `deliveries/audit-p0-20261007/`, `deliveries/account-lifecycle-20261007/` and matching `/opt/wb-saas/releases/` directories. Private inventory proofs contain hashes/metadata only; do not publish customer amounts or credentials.

## Not closed by these deployments

- Owner rotation of historically exposed Telegram bot token still requires confirmation through the secret-management channel.
- Native store packages, signing/developer accounts, native push/download/device acceptance and store payment policy remain prerequisites; no mobile app is claimed published.
- Legal retention/disclosures, fiscal-receipt configuration and registration evidence require owner/accountant/legal evidence.
- Partial-refund access semantics, Ozon participant numeric-ID selection, large WB bundle and stress-period latency require separate acceptance/fixes.
- Trial/paid capacity budgets, idle-stop/start lifecycle, wildcard DNS-01 certificates, account-level login throttling and legacy-source removal are not marked closed by these releases. Existing infrastructure limits are verified; proposed20–30-client capacity is not proven by a six-request test.
- Scoped live-volume purge is deployed, but no actual demo/mvvorokov/marketing deletion is an acceptance action. Backup expiry remains governed by separate retention; live-volume removal is not immediate removal of every retained copy.

Whole audit is not marked complete while these items remain.
