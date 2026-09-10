# Credential and Backup Encryption Implementation Plan

**Goal:** prevent seller credentials from being stored in plaintext in tenant volumes and ensure host-level tenant-volume backups are encrypted before offsite synchronization.

**Design:** gateway derives a unique 32-byte Fernet key for each tenant from a deployment-only master secret and passes only that derived key to its tenant container. Tenant fallback files use an authenticated versioned ciphertext; legacy plaintext is read once and re-written encrypted when the key is configured. Provisioner writes a temporary tar archive, encrypts it in chunks with AES-256-GCM using a separate backup passphrase, then removes the plaintext before reporting a `.tar.gz.enc` archive.

**Key handling:** `WB_SAAS_TENANT_ENCRYPTION_MASTER_KEY` and `WB_SAAS_BACKUP_ENCRYPTION_KEY` are operator-supplied random secrets in the deployment environment. Neither is committed, copied into backup archives, nor passed to tenants. A missing key must fail closed for a new fallback write or host backup; it must never silently create plaintext data.

## Tasks

- [ ] Add a tested per-tenant derivation helper in gateway and propagate only the derived key through the provisioner request contract.
- [ ] Add authenticated token-file encryption to tenant-app, direct `cryptography` dependency, legacy fallback migration, and tests for ciphertext, round-trip, migration, and key absence.
- [ ] Add a streaming authenticated backup codec in provisioner and replace plaintext host `.tar.gz` output with `.tar.gz.enc`; add decrypt/restore operator flow and policy tests.
- [ ] Document required secrets, rotation/recovery constraints, and encrypted backup verification in `.env.example` and `DEPLOY.md`.
- [ ] Regenerate Linux lock files, build images, audit dependencies, run all tests and frontend builds, then commit only P1-4 changes.
