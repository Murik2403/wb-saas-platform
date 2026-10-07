# P1 security boundaries — 2026-10-07

## Active runtime

The deployed cabinet entrypoint uses `mh-redesign-lab/tenant-api/api_server.py`.
Gateway identity verification remains ahead of request limits and the dashboard
work queue. Limits run before JSON/form parsers; streamed responses and byte
range headers are passed through unchanged.

- Request headers: 32 KiB, maximum 100 entries.
- Ordinary request bodies: 1 MiB, maximum 4096 ASGI body chunks.
- Gateway report email/Telegram multipart requests: 24 MiB total; the existing
  per-file PDF limit still applies independently.
- Tenant CSV/migration/Ozon movement imports: 10 MiB total; existing payload and
  decoded-file limits remain in place.
- Incident attachments: 4 MiB; marketplace context: 2 MiB.

Request bodies are collected only up to these bounds before parsing. This is
not an unbounded upload stream; limits do not buffer or rewrite responses.
Development CORS requires `MH_ENABLE_DEV_CORS=1` and an empty `TENANT_SLUG`.
Configured tenants cannot enable development CORS, including demo tenants.

Legacy token files require the existing valid Fernet key before their value is
returned. Normal automatic migration encrypts them with that same key. Missing
or malformed keys fail closed; no key is generated or rotated by this patch.

## Dormant legacy API

`tenant-app/api_server.py` remains a legacy implementation, not the current
production API entrypoint. Its localhost CORS now uses the same explicit local
opt-in policy. Its old write endpoints do not provide the current signed gateway
identity boundary. This is a dormant source-path limitation, not evidence of a
production authentication bypass. Do not expose or substitute this legacy API
as a production entrypoint without a separate authentication migration.

## Verification scope

All probes use synthetic local data. This document does not claim deployment,
real-token validation or live load measurements. The release operator must
include both active `request_limits.py` modules and verify the deployed revision.
