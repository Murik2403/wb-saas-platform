# Identified host backups (WBSAASBK2)

The provisioner now writes host archives with an authenticated, nonsecret key ID.
This release does not generate, change, rotate or remove encryption keys. With no
new configuration, it uses the existing `WB_SAAS_BACKUP_ENCRYPTION_KEY` and ID
`legacy`. Existing `WBSAASBK1` archives remain readable with the same secret.
The tenant application's separate `mh-backup-enc:v2` format is unchanged.

## Wire format and limits

`WBSAASBK2 | uint8 ID length | ASCII ID | 16-byte salt | 12-byte nonce |
ciphertext | 16-byte GCM tag`. The entire header is AES-GCM additional
authenticated data; changing an ID to another configured ID still fails tag
verification. Scrypt parameters remain unchanged from v1. Reading, encryption and
decryption are streamed in 1 MiB chunks, with a 120-second processing deadline.
Temporary output is created exclusively with mode 0600; only a verified complete
plaintext replaces the destination. Failed authentication removes that operation's
temporary plaintext and preserves an existing destination or another writer's file.

IDs allow 1–64 ASCII letters, digits, `_`, `-`, `.` and start with a letter or digit.
A keyring contains at most 16 entries, 64 KiB total configuration and 32–4096
characters per secret. Invalid and duplicate IDs, null/array keyrings, unknown IDs
and conflicting current-ID values fail; errors never include secret values.

## Configuration for a future operator-controlled rotation

- `WB_SAAS_BACKUP_ENCRYPTION_KEY`: current secret, unchanged by this release.
- `WB_SAAS_BACKUP_ENCRYPTION_KEY_ID`: current ID, default `legacy`.
- `WB_SAAS_BACKUP_ENCRYPTION_KEYRING`: JSON object of historical ID-to-secret
  mappings, supplied through the existing secret delivery mechanism.
- `WB_SAAS_BACKUP_LEGACY_KEY_ID`: optional explicit historical ID for v1 archives
  that have no stored ID. By default v1 uses the current secret. It never tries
  every key or guesses the archive's key.

Keep each historical secret until all dependent archives expire under an approved
policy. Never assign an existing ID to a different secret. Before an actual
rotation, preserve the old secret under its existing ID, give the new secret a new
ID, explicitly select the old ID for pre-rotation v1 archives, and prove restores
of both old and new archives. No automatic rotation is enabled here.

## Deployment dependency and verification

Deploy host `scripts/backup_offsite.py`, `backup_offsite_envelope.py`,
`pdn_retention.py` and `pdn_retention_job.py` before enabling the new provisioner
writer. Offsite copies still wrap the inner v1/v2 archive in `MHOFFSITE2`; deletion
policies and the fixed historical rehearsal cleanup manifest are unchanged.
The provisioner image includes `backup_crypto.py` and `decrypt_backup.py`.
Deploying only the writer while retaining v1-only host scripts breaks offsite
format checks. Older provisioner images cannot decrypt newly written v2 archives;
keep the new decrypt helper available if rolling back the runtime.

Acceptance: create a fresh encrypted backup without changing the production key,
check its magic is `WBSAASBK2`, restore and run SQLite integrity checks, then verify
offsite copy/check and the recovery probe on that archive. Preserve existing
archives; this verification is not permission to rotate keys or delete backups.
Synthetic test coverage includes an independently generated deployed-v1 fixture,
v2 header tampering, unknown IDs, old-key selection, truncation, temp collisions,
empty payloads, helper configuration and SQLite archive restore.
