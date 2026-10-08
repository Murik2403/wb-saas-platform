# Selective audit and mobile integration plan

Goal: integrate useful DeepSeek changes and Claude mobile work, preserving stronger production security and data.

- [x] Resolve mobile merge while retaining erasure ledger protection.
- [x] Port stable ingestion IDs and POST email confirmation with regression tests.
- [x] Stop rollout at first failure; add HTTPS-only HSTS.
- [ ] Verify gateway, tenant API, tenant app, scripts, frontend and mobile checks.
- [ ] Commit source, deploy gateway then demo and mvvorokov with backup and rollback gates.
- [ ] Verify live HTTP/security/UI contracts and record remaining store publication requirements.
- [ ] Remove only identified generated scratch artifacts, preserving foreign work and backups.

Excluded: DeepSeek weaker identity/bearer and manager restrictions; store publication and unsigned app release; financial-data corrections without evidence.

Validation before release: gateway 558 passed / 5 skipped plus 38 final focused checks; tenant API 658; tenant app 1173 / 1 skipped plus 11 final focused checks; frontend 595; scripts 204 / 3 skipped plus 3 fail-fast cases. Frontend production build and mobile typecheck/build passed. Mobile npm audit: zero vulnerabilities after tested dependency overrides. Android cap sync/doctor passed; iOS packaging requires Xcode on macOS.
Cleanup: removed two generated Android example tests. Foreign dirty main checkout, historical archives, backups, working mobile screenshot sources and signing material are outside cleanup scope.
