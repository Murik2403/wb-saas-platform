# P1 control-plane source contract

The production compose configuration separates three internal networks:

- `control_api_net`: gateway and provisioner only. Provisioner has the `provisioner` DNS alias.
- `docker_control_net`: provisioner and writable Docker socket proxy only. No gateway, Traefik or tenant attaches here.
- `docker_discovery_net`: Traefik and the separate read-only Docker discovery proxy only. Discovery `POST=0`; the writable proxy has `POST=1` and is not on the edge network.

Root deployment must create the new networks, replace socket proxies, move gateway/provisioner and update Traefik through the existing host `traefik_up.sh` procedure, retaining every tenant network attachment. A generic compose Traefik replacement can drop those attachments. Runtime network migration is not proven by source tests. The read-only discovery service can still inspect Docker metadata/environment required by discovery; it remains a trusted private Traefik boundary, not a confidentiality filter.

`WB_SAAS_PROVISIONER_BEARER_KEY` is required by compose, present only in gateway/provisioner and never included in tenant environment payloads. Root derives it via `internal_auth.provisioner_credential(gateway_master)` with a distinct HMAC purpose. Every `/v1/*` provisioner request requires the exact Bearer credential using constant-time comparison; missing configuration rejects requests503. `/healthz` remains readable for local readiness. Gateway lifecycle/export, backup scripts, demo reseed and credential-check callers send authenticated headers. Bootstrap ordering: configure the derived key on both services before enabling new server middleware; preserve private networks throughout migration.

Retained encryption keyrings use explicit tenant namespaces: `{"slug":{"old-key-id":"canonical-Fernet-key"}}`. `provisioning.tenant_keyring_for_slug(slug)` returns only that slug's flat ring. Nonempty global flat rings are rejected; they must be explicitly mapped by the operator to the matching tenant without changing key values/IDs. Master and other tenants' retained keys never enter a tenant payload. The release key-derivation helper uses this same scoped function. Tests decrypt a retained ciphertext with the exact selected old key, proving no key-format migration or regeneration is required.

Frontend staging binds only127.0.0.1 and runs UID10001 with a read-only root, dropped capabilities and dedicated writable nginx tmpfs. This is source hardening; root owns runtime staging acceptance and image pins.
