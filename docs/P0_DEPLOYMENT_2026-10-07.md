# P0 production release, 2026-10-07

## Confirmed owner policy

The owner explicitly confirmed: managers retain product, price and production work. Marketplace API credentials, Telegram recipient linking, team access, backups and data deletion are owner-only. Anonymous demo is always viewer. Production tenant API accepts forwarded identity only with a short-lived, tenant-scoped HMAC proof bound to method, raw URI, role and account.

## Deployed committed source

Revision: `480ff198ad08852acb70ed597fdb8879a66780e8`.

| Runtime | Immutable image |
| --- | --- |
| Gateway | `sha256:58c8d5e8f5cba259dae88f4f16743e0635830c9c7eb24fac6586cab44673e6c7` |
| Provisioner | `sha256:4f9c9e9a4f8f51f99a997c18431c80c64712edcdb42b5259ae031dca89594e08` |
| demo, mvvorokov, marketing | `sha256:28d28007aec51bffd304cff7423f5cb452e320d2cd30f41899ea5f711dc1b6eb` |

The frontend was exported from the committed revision and built with `VITE_MH_API_BASE_URL=''` (same-origin production API). The earlier incorrect build used the unrelated `VITE_API_BASE` variable and was replaced after browser acceptance caught HTML responses. The persistent tenant was never switched to that faulty frontend.

All tenant volume mounts and network aliases were preserved. Tenant memory remains 1536 MiB. Runtime log rotation is 10 MiB × 3. Fresh encrypted tenant backups were independently restored and SQLite integrity/table counts verified before each switch. Gateway backup restoration and rollback-image rehearsal also passed. Provisioner configuration was pinned to the new tenant image. Its old demo reconciler had returned demo to the old image; after advancing the pin, demo was redeployed and the three live image IDs checked again.

## Acceptance

- Clean committed tenant-app: 1028 passed, 1 skipped, 10 subtests passed.
- Clean committed frontend: 551 passed; TypeScript and production build passed.
- Prior isolated gates for unchanged Python runtime inputs: gateway 467 passed, 2 skipped, 203 subtests; tenant API 627 passed; provisioner 60 passed.
- Actual owner browser session loads WB settings, retains connected WB/Ozon state and enables owner credential controls.
- Live mvvorokov and marketing API: unsigned owner and forged role proof → 403; signed manager/viewer settings read → 200; signed manager backup list, WB connection, Ozon connection and Telegram link-code requests → 403. Write probes supplied empty bodies; no marketplace changes were performed.
- Public demo and mvvorokov health endpoints → `{"status":"ok"}`.
- Gateway formatter, Sentry event and breadcrumb redaction verified inside the live container; no outgoing test Sentry event. Since-start secret URL log count: 0.

Detailed immutable-source manifests, restore/deployment proofs and authorization status records are in the local workspace `deliveries/audit-p0-20261007/release/`; server copies are in `/opt/wb-saas/releases/p0-480ff19/`. Owner settings screenshot: `deliveries/audit-p0-20261007/owner-settings-live-480ff19.jpg`.

## Still open

- Host cron/offsite encryption, freshness alarms and migration of legacy plaintext backups need independent production acceptance. Deployed backup-helper changes alone do not close this item.
- Owner must reissue the historically exposed Telegram bot token through the normal credential-management channel; never paste it into chat.
- Onboarding connection controls need the same visible owner-only policy as the main settings panel; server authorization already rejects restricted attempts.
- P1 and P2 remain separate, undeployed tranches. This report does not mark the entire audit complete.
