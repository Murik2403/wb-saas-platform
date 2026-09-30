# Audit tranche: deployed 2055a4a

The whole audit remains open. This receipt closes the integrated shared-warehouse lifecycle, signed financial projection, selected-shop financial refresh and opt-in secondary-shop schedules for this bounded release.

## Exact identities and recovery

- Tenant source: `2055a4ac61984ca4317d151bd629818c269c51b5`.
- Image tag: `mh-tenant:2055a4a`; Docker image ID: `sha256:f5a1b0468207e76ae0bb36181b9ea489d242b5407fa7292f7df68c1ec166947e`.
- Native rehearsal backup: `marketshelper_manual_20260930_062342_266389.zip`. Candidate and old-image clone checks passed after verified encrypted restoration. Production remained untouched by the rehearsal.
- Deployment backup: `marketshelper_manual_20260930_063603_356776.zip`; retained rollback: `wb-tenant-mvvorokov-rollback-20260930-063603`.
- Gateway live source: `c4e15baf3c065d2e25266273346df6bb93466412`. Fresh owner membership and unknown-account denial accepted again after tenant deployment.
- Provisioner remains `0eb4746d06dcc9356ca95b26a7096efb014495c6`, exact `sha256:99fd4efe198af2b757ff3c2aa407e898bb349ea842ea3a1450cfb77aac4bab72`, now pinned to the exact tenant image above. Default `wb-dashboard-tenant:redesign` points to the same image.
- Provisioner backup: `gateway-provisioner-0eb4746d06dc-1790750267074341071.sqlite3.enc`; rollback: `wb-saas-provisioner-1-rollback-1790750267999794036`. Demo setting preserved, not changed.

## Source and exact-image verification

- Combined tenant/report suites: 673 passed, 8 subtests passed, 1 Linux-only test skipped on Windows. API: 402 passed. Integrated frontend: 201 passed across 35 files; production build passed. Source imports: 25; clean release gate: 18/18, zero tracked sensitive files.
- Exact Linux image acceptance: actual executor parent killed while the living child retained its inherited slot; second worker denied until child exit. Two actual selected-shop PDF files, distinct SHA256, local archive, encrypted backup/restore, due replay and owner disable after ACL revocation accepted. All ten imported source hashes independently matched the accepted checkout. Synthetic current-role fixture is explicit; no live WB sync or marketplace identity verification claimed.
- Exact-image native five-business scenario: material units m/l/pieces, two procurement prices, BOM snapshot/replay, release cost 60 RUB (30/unit), signed sale/return COGS 30, accounting profit 50; day/month expenses 25/315; imported external expense 30; management profit -295; cash payment exactly 160 once. CSV preview, confirm, replay/re-upload and no duplicate WB fee accepted.
- Exact-image shared lifecycle: concurrent allocation, immutable mapping receipts, strict roles/DTOs, foreign-document denial, dispatch/arrival/sale/partial return, quarantine, signed financial projection, changed/missing primary seller denial and source downgrade accepted. Preview and financial linking did not duplicate physical movements.
- Restored-data concurrent API peak: 445325312 bytes under the unchanged 1610612736-byte limit; no OOM, restart or memory-limit increase. Cash and manual Ozon workflows passed on the isolated clone; this does not establish automatic Ozon API normalization.

## Actual browser and live verification

Fresh local fixtures used detached exact source and dist extracted from the exact image, with new synthetic data, no real keys, no external network access and local jobs disabled. The actual browser completed registration, mapping, reserve dated September 10, dispatch, arrival, sale 3 units at 1.01 RUB, partial return 1 unit at 0.34 RUB into quarantine, and two financial links. Missing return proof was denied. Both financial hero and management result displayed 199 RUB without manual refresh; raw profit was 199.33, signed COGS 0.67. BETA had no documents/holds. These are teaching facts, not live marketplace evidence.

Both schedules started disabled at generation zero. Saving a disabled PDF configuration produced generation one, still disabled and with no attempts. An unbound-primary fixture exposed no movement form; actual seller verification failed under the offline guard, cleared the primary context and left identity null and all physical tables empty.

Production returned `LIVE_API_17_HTML_ASSETS_OK` and `LIVE_REVISION_HEALTH_OK` for the exact revision. The authenticated owner browser loaded `/assets/index--Qrrp2EQ.js` and showed 986738 RUB explicitly as preliminary WB result. No owner expense attestations or synthetic ledger records were written to production.

## Remaining audit work

Finished-lot/historical-document migration is a separate implementation in progress. Automatic Ozon monetary normalization requires supported nonempty primary contracts and reconciliation. Real case publication, final video acceptance, actual interviews and conditional marking retain their own evidence/authorization requirements. Shared financial binding remains one report row to one canonical document; grouped/revised bindings are not claimed. Secondary-shop reads serialize private child processes; observed cold/fanout latency is being investigated separately.

Server storage reached 384 MB free after building. Only unused builder cache was pruned, freeing about 1.36 GB. Production volumes, backups and retained rollback containers were not removed. Capacity remains a release constraint for subsequent builds.
