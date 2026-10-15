# PDF budget and training backup rollout

Owner supplied `claude/lessons-backup-20261018` (69e0fbe) and `claude/pdf-budget-20261019` (eaecdba) for the combined release. Existing verified production tenants are 3dc30c3; gateway/provisioner are b13fa97. No new simultaneous density acceptance is implied.

- [x] Merge both branches while retaining optimization 89ad911 and current audit evidence.
- [x] Independent review and meaningful regression fixes: purge scope for shared mount; fail closed on configured unavailable host slots; request wait bound; archive manifest/member validation and gateway rehearsal environment isolation.
- [x] Fresh targeted/full suites and graph update; commit scoped fixes before deriving any release image.
- [x] Freeze git archive and SHA from the exact committed HEAD. Preserve each production frontend and dependencies; derive tenant images and provisioner image with only the required committed modules.
- [x] Validate candidate and rollback against isolated encrypted snapshots, migrations and financial parity. Validate Linux shared flock, waiting queue and killed-lock holder recovery without production/provider writes.
- [x] First production training pack/verify with the existing backup key and gateway host read-only media source; independent restored-file and isolated gateway verification. Retain old archives and current lessons.
- [x] Stage and validate Compose/environment image pins and PDF host directory. Preserve existing mounts, aliases, resource limits, credentials and retained rollback containers. Roll out provisioner and tenants sequentially; configured budget must be healthy in every tenant.
- [x] Verify live business health, queue/slots diagnostics, finance and lessons; authenticated offsite archive readback; bounded recurring backup integration only after independent tests.
- [x] Update actual audit evidence and remaining acceptance boundaries. Do not claim full new-host DR, simultaneous 25/30 capacity, or delivery/provider acceptance from queued PDF tests.

Actual production revision: `73c6a34b8ee2940a25fe40ac8c65c2e06672c02d`; [acceptance](../../PDF_LESSONS_PRODUCTION_ACCEPTANCE_2026-10-15.md). Prior failed rollout was completely rolled back and its evidence retained; readiness operators now wait for Traefik route convergence without weakening health assertions. The scheduled first cron firing is not claimed from a manual helper/watchdog run.

Parent owns SSH and runtime changes. Agents own scoped source/tests; no real payments, Telegram enablement, WB/Ozon calls, production reboot, old unknown archive deletion or lesson replacement.
